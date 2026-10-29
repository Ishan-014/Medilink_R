"""Recover local state from authoritative external receipts without replaying writes."""

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from psycopg2.extras import RealDictCursor
from starlette.concurrency import run_in_threadpool

from backend.api.schemas import Role
from backend.core.errors import BackendError
from backend.db.connection import connection
from backend.integrations.client import checked_json
from backend.recovery.client import validate_config
from backend.recovery.contracts import LookupRequest, LookupResult, ReconciliationRecord
from backend.services.workflow_service import load, check_version


def record(row):
    return ReconciliationRecord(
        **{key: row[key] for key in ReconciliationRecord.model_fields}
    )


def prepare(settings, workflow_id, call_id, user, body):
    if user["role"] != Role.ADMIN:
        raise HTTPException(403, "Administrator reconciliation permission required")
    validate_config(settings)
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            workflow = load(cursor, workflow_id, user, lock=True)
            check_version(workflow, body.expected_version)
            cursor.execute(
                """SELECT t.*,k.idempotency_key,k.fingerprint AS reserved_digest,k.status AS reservation_status,
                a.status AS approval_status,a.consumed_call_id
                FROM tool_calls t JOIN workflow_action_keys k ON k.id=t.action_reservation_id
                JOIN approvals a ON a.id=t.approval_id
                WHERE t.id=%s AND t.workflow_id=%s FOR UPDATE OF t,k,a""",
                (str(call_id), workflow_id),
            )
            call = cursor.fetchone()
            if not call or call["status"] not in {"pending", "uncertain"}:
                raise HTTPException(
                    409, "Only unresolved approved external writes can be reconciled"
                )
            operation = f"{call['server']}.{call['tool']}"
            if (
                operation not in {"ehr.create_patient", "insurance.submit_claim"}
                or call["approval_status"] != "consumed"
                or str(call["consumed_call_id"]) != str(call_id)
                or call["reserved_digest"] != call["request_fingerprint"]
                or call["reservation_status"] not in {"pending", "uncertain"}
            ):
                raise HTTPException(
                    409, "External write has inconsistent execution records"
                )
            if call["status"] == "pending" and call["started_at"] > datetime.now(
                timezone.utc
            ) - timedelta(seconds=settings.reconciliation_stale_seconds):
                raise BackendError("RECOVERY_NOT_READY")
            # Only lookups are repeatable. A lost lookup worker may be superseded
            # after its bounded deadline; its stale response cannot finalize later.
            cursor.execute(
                """UPDATE external_reconciliations SET status='failed',error_code='RECOVERY_TIMEOUT',finished_at=clock_timestamp()
                WHERE tool_call_id=%s AND status='pending' AND started_at < clock_timestamp() - %s * interval '1 second'""",
                (str(call_id), settings.recovery_timeout_seconds + 60),
            )
            cursor.execute(
                "SELECT 1 FROM external_reconciliations WHERE tool_call_id=%s AND status='pending'",
                (str(call_id),),
            )
            if cursor.fetchone():
                raise HTTPException(409, "A receipt lookup is already in progress")
            from backend.services.business_service import expected_arguments

            args = expected_arguments(cursor, workflow_id, operation)
            if (
                args is None
                or hashlib.sha256(
                    checked_json(args, 65536, "MCP_INPUT_INVALID")
                ).hexdigest()
                != call["request_fingerprint"]
            ):
                raise HTTPException(
                    409,
                    "Persisted action arguments no longer match the dispatched write",
                )
            reconciliation_id = str(uuid4())
            cursor.execute(
                """INSERT INTO external_reconciliations
                (id,workflow_id,tool_call_id,actor_user_id,reason,previous_status,original_error_code)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (
                    reconciliation_id,
                    workflow_id,
                    str(call_id),
                    user["user_id"],
                    body.reason,
                    call["status"],
                    call["error_code"],
                ),
            )
            cursor.execute(
                "UPDATE workflow_runs SET version=version+1,updated_at=clock_timestamp() WHERE id=%s",
                (workflow_id,),
            )
            return LookupRequest(
                reconciliation_id=reconciliation_id,
                workflow_id=workflow_id,
                tool_call_id=call_id,
                approval_id=call["approval_id"],
                operation=operation,
                idempotency_key=call["idempotency_key"],
                arguments_digest=call["request_fingerprint"],
            )


def finalize(settings, request, *, result=None, error=None):
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            workflow_id = str(request.workflow_id)
            cursor.execute(
                "SELECT * FROM workflow_runs WHERE id=%s FOR UPDATE", (workflow_id,)
            )
            workflow = cursor.fetchone()
            cursor.execute(
                "SELECT * FROM external_reconciliations WHERE id=%s FOR UPDATE",
                (str(request.reconciliation_id),),
            )
            attempt = cursor.fetchone()
            if not attempt or attempt["status"] != "pending":
                raise HTTPException(
                    409, "Receipt lookup has already finished or been superseded"
                )
            cursor.execute(
                "SELECT * FROM tool_calls WHERE id=%s FOR UPDATE",
                (str(request.tool_call_id),),
            )
            call = dict(cursor.fetchone())
            if call["status"] not in {"pending", "uncertain"}:
                error = BackendError("RECOVERY_NOT_READY")
            reference = None
            if not error:
                # Validate even injectable clients at the service boundary.
                try:
                    result = LookupResult.model_validate(result.model_dump())
                    if (
                        result.model_dump(include=set(LookupRequest.model_fields))
                        != request.model_dump()
                    ):
                        raise ValueError("Receipt bindings changed")
                except (ValueError, TypeError, AttributeError) as exc:
                    raise BackendError("RECOVERY_OUTPUT_INVALID") from exc
                reference = result.external_reference
                if result.outcome in {"succeeded", "not_applied"}:
                    from backend.services.business_service import (
                        expected_arguments,
                        close_case,
                    )

                    args = expected_arguments(cursor, workflow_id, request.operation)
                    if (
                        args is None
                        or hashlib.sha256(
                            checked_json(args, 65536, "MCP_INPUT_INVALID")
                        ).hexdigest()
                        != request.arguments_digest
                    ):
                        raise BackendError("RECOVERY_OUTPUT_INVALID")
                    success = result.outcome == "succeeded"
                    call_status = "succeeded" if success else "failed"
                    call_reference = (
                        f"tool-call:{request.tool_call_id}" if success else None
                    )
                    cursor.execute(
                        """UPDATE tool_calls SET status=%s,error_code=%s,result_reference=%s,finished_at=clock_timestamp()
                        WHERE id=%s RETURNING *""",
                        (
                            call_status,
                            None if success else "EXTERNAL_NOT_APPLIED",
                            call_reference,
                            str(request.tool_call_id),
                        ),
                    )
                    call = dict(cursor.fetchone())
                    cursor.execute(
                        """UPDATE workflow_action_keys SET status=%s,result_reference=%s,updated_at=clock_timestamp()
                        WHERE id=%s""",
                        (
                            call_status,
                            call_reference,
                            str(call["action_reservation_id"]),
                        ),
                    )
                    cursor.execute(
                        "UPDATE workflow_steps SET status=%s,result_reference=%s,updated_at=clock_timestamp() WHERE id=%s",
                        (
                            "COMPLETED" if success else "FAILED",
                            call_reference,
                            str(call["workflow_step_id"]),
                        ),
                    )
                    if success:
                        if workflow["status"] not in {"EXECUTING", "FAILED"}:
                            raise BackendError("RECOVERY_NOT_READY")
                        cursor.execute(
                            "UPDATE patient_profiles SET status='draft' WHERE onboarding_workflow_id=%s AND status='failed'",
                            (workflow_id,),
                        )
                        cursor.execute(
                            "UPDATE claims SET status='prepared' WHERE workflow_id=%s AND status='failed'",
                            (workflow_id,),
                        )
                        from backend.services.business_evidence import capture

                        try:
                            capture(
                                cursor, call, args, result.result, settings=settings
                            )
                            cursor.execute(
                                "SELECT status FROM workflow_runs WHERE id=%s",
                                (workflow_id,),
                            )
                            if cursor.fetchone()["status"] != "COMPLETED":
                                raise ValueError(
                                    "Receipt did not confirm the domain change"
                                )
                        except (HTTPException, ValueError) as exc:
                            raise BackendError("RECOVERY_OUTPUT_INVALID") from exc
                    else:
                        close_case(cursor, workflow_id, "failed")
            status = (
                "failed"
                if error
                else {
                    "succeeded": "succeeded",
                    "not_applied": "not_applied",
                    "pending": "unresolved",
                }[result.outcome]
            )
            cursor.execute(
                """UPDATE external_reconciliations SET status=%s,external_reference=%s,error_code=%s,
                finished_at=clock_timestamp() WHERE id=%s RETURNING *""",
                (
                    status,
                    reference,
                    error.error_code if error else None,
                    str(request.reconciliation_id),
                ),
            )
            row = cursor.fetchone()
            cursor.execute(
                "UPDATE workflow_runs SET version=version+1,updated_at=clock_timestamp() WHERE id=%s",
                (workflow_id,),
            )
            cursor.execute(
                """INSERT INTO system_events(workflow_id,request_id,actor_user_id,component,code,severity)
                VALUES (%s,nullif(current_setting('medilink.request_id',true),''),%s,'recovery',%s,%s)""",
                (
                    workflow_id,
                    attempt["actor_user_id"],
                    error.error_code if error else "RECOVERY_" + status.upper(),
                    "error" if error else "info",
                ),
            )
            return record(row)


async def reconcile(settings, client, workflow_id, call_id, user, body):
    request = await run_in_threadpool(
        prepare, settings, workflow_id, call_id, user, body
    )
    try:
        async with asyncio.timeout(settings.recovery_timeout_seconds):
            result = await client.lookup(settings, request)
        return await run_in_threadpool(finalize, settings, request, result=result)
    except asyncio.CancelledError:
        await asyncio.shield(
            run_in_threadpool(
                finalize, settings, request, error=BackendError("RECOVERY_UNAVAILABLE")
            )
        )
        raise
    except HTTPException as exc:
        error = (
            exc
            if isinstance(exc, BackendError)
            else BackendError("RECOVERY_OUTPUT_INVALID")
        )
        await run_in_threadpool(finalize, settings, request, error=error)
        raise error from exc
    except Exception as exc:
        error = BackendError(
            "RECOVERY_TIMEOUT"
            if isinstance(exc, TimeoutError)
            else "RECOVERY_UNAVAILABLE"
        )
        await run_in_threadpool(finalize, settings, request, error=error)
        raise error from exc


def list_attempts(settings, workflow_id, user, limit, offset):
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            load(cursor, workflow_id, user)
            cursor.execute(
                "SELECT * FROM external_reconciliations WHERE workflow_id=%s ORDER BY started_at,id LIMIT %s OFFSET %s",
                (workflow_id, limit, offset),
            )
            return [record(row) for row in cursor.fetchall()]
