"""Authorize reads and single-use approved writes through one backend boundary."""

import asyncio
import hashlib
import time
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg2.extras import RealDictCursor
from pydantic import Field, JsonValue
from starlette.concurrency import run_in_threadpool

from backend.api.schemas import Contract, Role
from backend.db.connection import connection
from backend.integrations.client import checked_json
from backend.integrations.config import configured_server
from backend.integrations.errors import IntegrationError
from backend.integrations.policy import split_operation, WRITE_TOOLS
from backend.services.workflow_service import load as load_workflow, check_version
from backend.risk.policy import classify, ENABLED_WRITES
from backend.risk.context import action_key, execution_context
from backend.services import approval_service


class ToolCallInput(Contract):
    expected_version: int = Field(ge=0, strict=True)
    step_id: UUID
    operation: str = Field(min_length=3, max_length=150, pattern=r"^[a-z]+\.[A-Za-z0-9_]+$")
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ToolCallRecord(Contract):
    id: UUID
    workflow_id: UUID
    workflow_step_id: UUID
    actor_user_id: str
    server: str
    tool: str
    status: Literal["pending", "succeeded", "failed", "uncertain"]
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    error_code: str | None
    result_reference: str | None
    risk: Literal["low", "medium", "high"]
    approval_id: UUID | None
    action_reservation_id: UUID | None


class ToolCallOutput(Contract):
    call: ToolCallRecord
    result: JsonValue


def record(row):
    return ToolCallRecord(**{key: row[key] for key in ToolCallRecord.model_fields})


def prepare(settings, workflow_id, user, body):
    if user["role"] not in {Role.DOCTOR, Role.ADMIN}:
        raise HTTPException(403, "Doctor or administrator execution permissions required")
    selection = split_operation(body.operation)
    if selection is None:
        raise HTTPException(403, "Hospital capability is not allowed")
    server_name, tool_name = selection
    is_write = tool_name in WRITE_TOOLS.get(server_name, set())
    if is_write and body.operation not in ENABLED_WRITES:
        raise IntegrationError("MCP_WRITE_BLOCKED")
    risk = classify(body.operation)
    payload = checked_json(body.arguments, 65536, "MCP_INPUT_INVALID")
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            workflow = load_workflow(cursor, workflow_id, user, lock=True)
            check_version(workflow, body.expected_version)
            approval = None
            context = None
            reservation_id = None
            if is_write:
                if workflow["managed_kind"] != ENABLED_WRITES[body.operation]:
                    raise IntegrationError("MCP_WRITE_BLOCKED")
                approval, canonical = approval_service.authorized(cursor, workflow, body.step_id, settings)
                if body.arguments != canonical:
                    raise HTTPException(409, "Arguments differ from the approved action")
                from backend.services.business_service import assert_no_unresolved_write
                assert_no_unresolved_write(cursor, workflow["subject_user_id"], body.operation)
                action_key(settings)
            server = configured_server(settings, server_name)
            if workflow["status"] != "EXECUTING":
                raise HTTPException(409, "Workflow must be EXECUTING before calling hospital tools")
            if workflow["managed_kind"]:
                from backend.services.business_service import expected_arguments
                expected = expected_arguments(cursor, workflow_id, body.operation)
                if expected is None or expected != body.arguments:
                    raise HTTPException(409, "Tool arguments must match the persisted case intake")
            cursor.execute("SELECT * FROM workflow_steps WHERE id = %s AND workflow_id = %s FOR UPDATE",
                           (str(body.step_id), workflow_id))
            step = cursor.fetchone()
            if not step or step["action"] != body.operation:
                raise HTTPException(409, "Tool call must match its persisted workflow step")
            if step["status"] != "CREATED":
                raise HTTPException(409, "Workflow step has already started or finished")
            cursor.execute("""SELECT 1 FROM workflow_steps WHERE workflow_id = %s
                AND position < %s AND status <> 'COMPLETED' LIMIT 1""", (workflow_id, step["position"]))
            if cursor.fetchone():
                raise HTTPException(409, "Earlier workflow steps must complete first")
            cursor.execute("""SELECT 1 FROM workflow_documents wd JOIN documents d ON d.id = wd.document_id
                WHERE wd.workflow_id = %s AND d.extraction_status <> 'completed' LIMIT 1""", (workflow_id,))
            if cursor.fetchone():
                raise HTTPException(409, "Attached documents must finish extraction first")
            call_id = str(uuid4())
            digest = hashlib.sha256(payload).hexdigest()
            if is_write:
                reservation_id = str(uuid4())
                key = f"approved-step:{body.step_id}"
                cursor.execute("""INSERT INTO workflow_action_keys (id,workflow_id,action,idempotency_key,fingerprint)
                    VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
                    (reservation_id, workflow_id, body.operation, key, digest))
                if not cursor.fetchone():
                    raise HTTPException(409, "External action has already been reserved; reconcile its recorded outcome")
                context = execution_context(settings, approval, call_id, user["user_id"], body.operation,
                                            digest, key, server.timeout_seconds)
            cursor.execute("""
                INSERT INTO tool_calls (id, workflow_id, workflow_step_id, actor_user_id, server, tool,
                    request_fingerprint,risk,approval_id,action_reservation_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (call_id, workflow_id, str(body.step_id), user["user_id"], server_name, tool_name,
                  digest, risk, str(approval["id"]) if approval else None, reservation_id))
            if approval:
                approval_service.consume(cursor, approval, call_id, user["user_id"])
            cursor.execute("UPDATE workflow_steps SET risk=%s WHERE id=%s", (risk,str(body.step_id)))
            cursor.execute("UPDATE workflow_steps SET status = 'EXECUTING', updated_at = clock_timestamp() WHERE id = %s", (str(body.step_id),))
            cursor.execute("UPDATE workflow_runs SET version = version + 1, updated_at = clock_timestamp() WHERE id = %s", (workflow_id,))
    return call_id, server, tool_name, context


def finalize(settings, call_id, duration_ms, error: IntegrationError | None = None, *, arguments=None, result=None):
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT workflow_id FROM tool_calls WHERE id = %s", (call_id,))
            workflow_id = cursor.fetchone()["workflow_id"]
            # Same lock order as prepare/cancel: workflow, then the call/step.
            cursor.execute("SELECT status FROM workflow_runs WHERE id = %s FOR UPDATE", (str(workflow_id),))
            workflow_status = cursor.fetchone()["status"]
            cursor.execute("SELECT * FROM tool_calls WHERE id=%s FOR UPDATE", (call_id,))
            pending = cursor.fetchone()
            is_write = pending["tool"] in WRITE_TOOLS.get(pending["server"], set())
            if is_write:
                if not pending["approval_id"] or not pending["action_reservation_id"]:
                    raise IntegrationError("MCP_WRITE_BLOCKED")
                cursor.execute("""SELECT 1 FROM approvals a JOIN workflow_action_keys k ON k.id=%s
                    WHERE a.id=%s AND a.status='consumed' AND a.consumed_call_id=%s
                    AND a.workflow_step_id=%s AND a.operation=%s AND k.workflow_id=%s
                    AND k.action=a.operation AND k.fingerprint=%s AND k.status='pending'""",
                    (str(pending["action_reservation_id"]),str(pending["approval_id"]),call_id,
                     str(pending["workflow_step_id"]), f"{pending['server']}.{pending['tool']}", str(workflow_id), pending["request_fingerprint"]))
                if not cursor.fetchone():
                    raise IntegrationError("MCP_WRITE_BLOCKED")
            uncertain = error is not None and error.dispatched and (is_write or error.error_code in {"MCP_TIMEOUT", "MCP_UNAVAILABLE"})
            status = "succeeded" if error is None else ("uncertain" if uncertain else "failed")
            reference = f"tool-call:{call_id}" if error is None else None
            cursor.execute("""
                UPDATE tool_calls SET status = %s, finished_at = clock_timestamp(), duration_ms = %s,
                    error_code = %s, result_reference = %s WHERE id = %s AND status = 'pending' RETURNING *
            """, (status, duration_ms, error.error_code if error else None, reference, call_id))
            row = cursor.fetchone()
            if row is None:
                raise HTTPException(409, "Tool call has already been finalized")
            if is_write:
                cursor.execute("""UPDATE workflow_action_keys SET status=%s,result_reference=%s,updated_at=clock_timestamp()
                    WHERE id=%s""", (status,reference,str(row["action_reservation_id"])))
            cursor.execute("""UPDATE workflow_steps SET status = %s, result_reference = %s,
                updated_at = clock_timestamp() WHERE id = %s""",
                           ("COMPLETED" if error is None else "FAILED", reference, str(row["workflow_step_id"])))
            next_status = "FAILED" if error is not None and workflow_status == "EXECUTING" else workflow_status
            cursor.execute("""UPDATE workflow_runs SET status = %s, version = version + 1,
                updated_at = clock_timestamp() WHERE id = %s""", (next_status, str(workflow_id)))
            if workflow_status == "EXECUTING":
                if error is None:
                    from backend.services.business_evidence import capture
                    capture(cursor, row, arguments, result, settings=settings)
                else:
                    from backend.services.business_service import close_case
                    close_case(cursor, str(workflow_id), "failed")
            return record(row)


async def execute(settings, client, workflow_id, user, body):
    call_id, server, tool, context = await run_in_threadpool(prepare, settings, workflow_id, user, body)
    start = time.monotonic()
    try:
        result = await client.call(server, tool, body.arguments, **({"context": context} if context else {}))
    except IntegrationError as exc:
        await run_in_threadpool(finalize, settings, call_id, int((time.monotonic() - start) * 1000), exc)
        raise
    except asyncio.CancelledError:
        # A cancelled handler never reports success. Shield final persistence.
        error = IntegrationError("MCP_UNAVAILABLE", dispatched=True)
        await asyncio.shield(run_in_threadpool(finalize, settings, call_id, int((time.monotonic() - start) * 1000), error))
        raise
    except Exception as exc:
        # An unexpected adapter failure must not leave an untracked false success.
        error = IntegrationError("MCP_UNAVAILABLE", dispatched=True)
        await run_in_threadpool(finalize, settings, call_id, int((time.monotonic() - start) * 1000), error)
        raise error from exc
    try:
        completed = await run_in_threadpool(finalize, settings, call_id, int((time.monotonic() - start) * 1000),
                                           arguments=body.arguments, result=result)
    except IntegrationError as exc:
        # Domain validation rolls back the attempted success before recording failure.
        await run_in_threadpool(finalize, settings, call_id, int((time.monotonic() - start) * 1000), exc)
        raise
    return ToolCallOutput(call=completed, result=result)


def list_calls(settings, workflow_id, user, limit=50, offset=0):
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            load_workflow(cursor, workflow_id, user)
            cursor.execute("SELECT * FROM tool_calls WHERE workflow_id = %s ORDER BY started_at, id LIMIT %s OFFSET %s",
                           (workflow_id, limit, offset))
            return [record(row) for row in cursor.fetchall()]
