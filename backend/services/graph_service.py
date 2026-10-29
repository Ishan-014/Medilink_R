"""Workflow-bound graph reads with durable metadata and failure states."""

import asyncio
import hashlib
import time
from uuid import uuid4

from fastapi import HTTPException
from psycopg2.extras import RealDictCursor
from starlette.concurrency import run_in_threadpool

from backend.api.schemas import Role
from backend.db.connection import connection
from backend.graph.client import GraphError, validate_result
from backend.graph.contracts import GRAPH_OPERATIONS, GraphRequest, GraphQueryRecord, GraphQueryOutput
from backend.integrations.client import checked_json
from backend.services.business_service import get_profile, close_case, set_phase
from backend.services.workflow_service import load, check_version


def record(row):
    return GraphQueryRecord(**{field: row[field] for field in GraphQueryRecord.model_fields})


def evidence_ready(cursor, workflow_id):
    cursor.execute("""SELECT q.operation FROM workflow_graph_queries q JOIN workflow_steps s ON s.id=q.workflow_step_id
        WHERE q.workflow_id=%s AND q.status='succeeded' AND s.status='COMPLETED'
        AND s.result_reference='graph-query:' || q.id::text AND q.unresolved_dependencies=0""", (workflow_id,))
    return set(GRAPH_OPERATIONS) <= {row["operation"] for row in cursor.fetchall()}


def prepare(settings, workflow_id, user, body):
    if user["role"] not in {Role.DOCTOR, Role.ADMIN}:
        raise HTTPException(403, "Doctor or administrator graph permissions required")
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            workflow = load(cursor, workflow_id, user, lock=True)
            check_version(workflow, body.expected_version)
            if workflow["status"] != "EXECUTING":
                raise HTTPException(409, "Workflow must be executing before querying graph context")
            profile = get_profile(cursor, body.patient_profile_id, user, active=True, clinical=True)
            if workflow["subject_user_id"] is not None:
                if workflow["subject_user_id"] != profile["owner_user_id"]:
                    raise HTTPException(409, "Graph subject does not match the workflow")
            else:
                cursor.execute("SELECT role FROM public.users WHERE user_id::text=%s", (workflow["owner_user_id"],))
                owner = cursor.fetchone()
                if owner and owner["role"] == "patient" and workflow["owner_user_id"] != profile["owner_user_id"]:
                    raise HTTPException(409, "Graph subject does not match the workflow")
            policy_id = procedure_code = None
            if workflow["managed_kind"]:
                cursor.execute("SELECT kind,intake FROM workflow_cases WHERE workflow_id=%s", (workflow_id,))
                case = cursor.fetchone()
                if (not case or case["kind"] != "insurance_claim" or not case["intake"].get("use_graph")
                        or case["intake"]["patient_profile_id"] != str(body.patient_profile_id)):
                    raise HTTPException(409, "Graph query does not match the managed case")
                policy_id = case["intake"]["policy_id"]
                procedure_code = case["intake"]["procedure_code"]
            cursor.execute("SELECT * FROM workflow_steps WHERE id=%s AND workflow_id=%s FOR UPDATE", (str(body.step_id), workflow_id))
            step = cursor.fetchone()
            if not step or step["action"] != f"graph.{body.operation}" or step["status"] != "CREATED":
                raise HTTPException(409, "Graph query must match an unstarted planned step")
            cursor.execute("SELECT 1 FROM workflow_steps WHERE workflow_id=%s AND position<%s AND status<>'COMPLETED' LIMIT 1", (workflow_id, step["position"]))
            if cursor.fetchone():
                raise HTTPException(409, "Earlier workflow steps must complete first")
            cursor.execute("""SELECT 1 FROM workflow_documents wd JOIN documents d ON d.id=wd.document_id
                WHERE wd.workflow_id=%s AND d.extraction_status<>'completed' LIMIT 1""", (workflow_id,))
            if cursor.fetchone():
                raise HTTPException(409, "Documents must finish extraction first")
            request = GraphRequest(query_id=uuid4(), workflow_id=workflow_id, operation=body.operation,
                patient_id=profile["external_patient_id"], policy_id=policy_id, procedure_code=procedure_code,
                max_results=settings.graph_max_results)
            fingerprint = hashlib.sha256(checked_json(request.model_dump(mode="json"), 65536, "MCP_INPUT_INVALID")).hexdigest()
            cursor.execute("""INSERT INTO workflow_graph_queries
                (id,workflow_id,workflow_step_id,patient_profile_id,actor_user_id,operation,request_fingerprint)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""", (str(request.query_id), workflow_id, str(body.step_id), str(body.patient_profile_id),
                    user["user_id"], body.operation, fingerprint))
            cursor.execute("UPDATE workflow_steps SET status='EXECUTING',risk='low',updated_at=clock_timestamp() WHERE id=%s", (str(body.step_id),))
            cursor.execute("UPDATE workflow_runs SET version=version+1,updated_at=clock_timestamp() WHERE id=%s", (workflow_id,))
            return request


def finalize(settings, request, duration_ms, *, result=None, error=None):
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            workflow_id = str(request.workflow_id)
            cursor.execute("SELECT status,managed_kind FROM workflow_runs WHERE id=%s FOR UPDATE", (workflow_id,))
            workflow = cursor.fetchone()
            unresolved = sum(item.kind == "dependency" and not item.resolved for item in result.items) if result else None
            cursor.execute("""UPDATE workflow_graph_queries SET status=%s,finished_at=clock_timestamp(),duration_ms=%s,
                error_code=%s,result_reference=%s,graph_revision=%s,item_count=%s,unresolved_dependencies=%s
                WHERE id=%s AND status='pending' RETURNING *""", (
                    "failed" if error else "succeeded", duration_ms, error.error_code if error else None,
                    result.result_reference if result else None, result.graph_revision if result else None,
                    len(result.items) if result else None, unresolved, str(request.query_id)))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(409, "Graph query has already been finalized")
            cursor.execute("""UPDATE workflow_steps SET status=%s,result_reference=%s,updated_at=clock_timestamp()
                WHERE id=%s""", ("FAILED" if error else "COMPLETED", None if error else f"graph-query:{request.query_id}", str(row["workflow_step_id"])))
            cursor.execute("UPDATE workflow_runs SET version=version+1,updated_at=clock_timestamp() WHERE id=%s", (workflow_id,))
            if workflow["status"] == "EXECUTING":
                if error:
                    if workflow["managed_kind"]:
                        close_case(cursor, workflow_id, "failed")
                    else:
                        cursor.execute("UPDATE workflow_runs SET status='FAILED' WHERE id=%s", (workflow_id,))
                elif workflow["managed_kind"] == "insurance_claim":
                    if unresolved:
                        set_phase(cursor, workflow_id, "waiting_data", ["graph_dependencies"])
                    elif evidence_ready(cursor, workflow_id):
                        from backend.services.business_service import CLAIM_READS
                        cursor.execute("SELECT operation FROM workflow_case_evidence WHERE workflow_id=%s", (workflow_id,))
                        if set(CLAIM_READS) <= {item["operation"] for item in cursor.fetchall()}:
                            set_phase(cursor, workflow_id, "awaiting_approval")
                            from backend.services.approval_service import enqueue
                            enqueue(cursor, workflow_id, row["actor_user_id"], settings)
            return record(row)


async def execute(settings, client, workflow_id, user, body):
    request = await run_in_threadpool(prepare, settings, workflow_id, user, body)
    started = time.monotonic()
    try:
        result = validate_result(request, await client.query(settings, request))
    except GraphError as exc:
        await run_in_threadpool(finalize, settings, request, int((time.monotonic()-started)*1000), error=exc)
        raise
    except asyncio.CancelledError:
        await asyncio.shield(run_in_threadpool(finalize, settings, request, int((time.monotonic()-started)*1000), error=GraphError("GRAPH_CANCELLED")))
        raise
    except Exception as exc:
        error = GraphError("GRAPH_UNAVAILABLE")
        await run_in_threadpool(finalize, settings, request, int((time.monotonic()-started)*1000), error=error)
        raise error from exc
    query = await run_in_threadpool(finalize, settings, request, int((time.monotonic()-started)*1000), result=result)
    return GraphQueryOutput(query=query, result=result)


def list_queries(settings, workflow_id, user, limit, offset):
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            load(cursor, workflow_id, user)
            cursor.execute("SELECT * FROM workflow_graph_queries WHERE workflow_id=%s ORDER BY started_at,id LIMIT %s OFFSET %s", (workflow_id, limit, offset))
            return [record(row) for row in cursor.fetchall()]
