"""Transactional workflow persistence. No tool execution or agent invocation."""

import hashlib
import json
from uuid import uuid4

from fastapi import HTTPException
from psycopg2.extras import Json, RealDictCursor

from backend.api.schemas import Role, WorkflowRun
from backend.db.connection import connection
from backend.services.document_service import link_documents

EDITORS = {Role.DOCTOR, Role.RECEPTIONIST, Role.ADMIN}
READERS = EDITORS | {Role.AUDITOR}
TRANSITIONS = {
    "CREATED": {"PLANNING", "CANCELLED"},
    "PLANNING": {"WAITING_FOR_DATA", "EXECUTING", "FAILED", "CANCELLED"},
    "WAITING_FOR_DATA": {"PLANNING", "FAILED", "CANCELLED"},
    "EXECUTING": {"WAITING_FOR_DATA", "WAITING_FOR_APPROVAL", "COMPLETED", "FAILED", "CANCELLED"},
    # Approval resumes/rejections need the authoritative Part 7 approval service.
    "WAITING_FOR_APPROVAL": {"CANCELLED"},
    "COMPLETED": set(), "FAILED": set(), "REJECTED": set(), "CANCELLED": set(),
}


def fingerprint(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def lock_key(cursor, scope: str):
    lock_id = int.from_bytes(hashlib.sha256(scope.encode()).digest()[:8], "big", signed=True)
    cursor.execute("SELECT pg_advisory_xact_lock(%s)", (lock_id,))


def check_access(row: dict, user: dict):
    # Return 404 for someone else's workflow rather than confirming it exists.
    if user["role"] not in READERS and row["owner_user_id"] != user["user_id"] and row.get("subject_user_id") != user["user_id"]:
        raise HTTPException(404, "Workflow not found")


def load(cursor, workflow_id: str, user: dict, *, lock=False):
    cursor.execute("SELECT * FROM workflow_runs WHERE id = %s" + (" FOR UPDATE" if lock else ""), (workflow_id,))
    row = cursor.fetchone()
    if row is None:
        raise HTTPException(404, "Workflow not found")
    check_access(row, user)
    return dict(row)


def serialize(cursor, row, *, loaded_steps=None) -> WorkflowRun:
    if loaded_steps is None:
        cursor.execute("SELECT * FROM workflow_steps WHERE workflow_id = %s ORDER BY position", (str(row["id"]),))
        loaded_steps=cursor.fetchall()
    steps = [
        {key: step[key] for key in ("id", "position", "action", "status", "risk", "result_reference")}
        for step in loaded_steps
    ]
    return WorkflowRun(**{
        key: row[key] for key in ("id", "owner_user_id", "workflow_type", "status", "created_at", "updated_at", "version", "document_ids", "managed_kind", "subject_user_id")
    }, steps=steps)


def get_workflow(settings, workflow_id: str, user: dict):
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            return serialize(cursor, load(cursor, workflow_id, user))


def list_workflows(settings, user, *, status=None, workflow_type=None, limit=50, offset=0):
    clauses, params = [], []
    if user["role"] not in READERS:
        clauses.append("(owner_user_id = %s OR subject_user_id = %s)")
        params.extend([user["user_id"], user["user_id"]])
    if status:
        clauses.append("status = %s")
        params.append(status.value)
    if workflow_type:
        clauses.append("workflow_type = %s")
        params.append(workflow_type.value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT * FROM workflow_runs" + where +
                           " ORDER BY created_at DESC, id LIMIT %s OFFSET %s", (*params, limit, offset))
            rows = cursor.fetchall()
            if not rows:
                return []
            cursor.execute("""SELECT * FROM workflow_steps WHERE workflow_id=ANY(%s::uuid[])
                ORDER BY workflow_id,position""",([str(row["id"]) for row in rows],))
            grouped={str(row["id"]):[] for row in rows}
            for step in cursor.fetchall():
                grouped[str(step["workflow_id"])].append(step)
            return [serialize(cursor,row,loaded_steps=grouped[str(row["id"])]) for row in rows]


def insert_workflow(cursor, body, user, key, *, payload=None):
    request_hash = fingerprint(payload if payload is not None else body.model_dump(mode="json"))
    if key:
        lock_key(cursor, f"workflow-create:{user['user_id']}:{key}")
        cursor.execute("""
            SELECT fingerprint, workflow_id FROM workflow_creation_keys
            WHERE owner_user_id = %s AND idempotency_key = %s
        """, (user["user_id"], key))
        prior = cursor.fetchone()
        if prior:
            if prior["fingerprint"] != request_hash:
                raise HTTPException(409, "Idempotency key was used for a different request")
            return load(cursor, str(prior["workflow_id"]), user), True
    workflow_id = str(uuid4())
    cursor.execute("""
        INSERT INTO workflow_runs (id, owner_user_id, workflow_type, request, document_ids)
        VALUES (%s, %s, %s, %s, %s) RETURNING *
    """, (workflow_id, user["user_id"], body.workflow_type.value, body.request,
          Json([str(d) for d in body.document_ids])))
    row = cursor.fetchone()
    link_documents(cursor, workflow_id, body.document_ids, user)
    if key:
        cursor.execute("""
            INSERT INTO workflow_creation_keys (owner_user_id, idempotency_key, fingerprint, workflow_id)
            VALUES (%s, %s, %s, %s)
        """, (user["user_id"], key, request_hash, workflow_id))
    return dict(row), False


def create_workflow(settings, body, user, key: str | None):
    if user["role"] == Role.AUDITOR:
        raise HTTPException(403, "Auditors have read-only access")
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            row, replayed = insert_workflow(cursor, body, user, key)
            return serialize(cursor, row), replayed


def check_version(row, expected):
    if row["version"] != expected:
        raise HTTPException(409, "Workflow changed; reload it before retrying")


def change_workflow(settings, workflow_id, user, body, *, plan=False):
    if user["role"] not in EDITORS:
        raise HTTPException(403, "Staff workflow permissions required")
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            row = load(cursor, workflow_id, user, lock=True)
            check_version(row, body.expected_version)
            if row["managed_kind"]:
                raise HTTPException(409, "Use the managed workflow endpoints for this case")
            if plan:
                if row["status"] != "PLANNING":
                    raise HTTPException(409, "Plans may only be supplied during PLANNING")
                cursor.execute("SELECT 1 FROM workflow_steps WHERE workflow_id = %s AND status <> 'CREATED' LIMIT 1", (workflow_id,))
                if cursor.fetchone():
                    raise HTTPException(409, "A plan with executed steps cannot be replaced")
                cursor.execute("DELETE FROM workflow_steps WHERE workflow_id = %s", (workflow_id,))
                for position, step in enumerate(body.steps):
                    cursor.execute("""
                        INSERT INTO workflow_steps (id, workflow_id, position, action)
                        VALUES (%s, %s, %s, %s)
                    """, (str(uuid4()), workflow_id, position, step.action))
                target = row["status"]
            else:
                target = body.status.value
                if target not in TRANSITIONS[row["status"]]:
                    raise HTTPException(409, "Invalid or approval-protected workflow transition")
                if target in {"EXECUTING", "COMPLETED"}:
                    cursor.execute("""
                        SELECT 1 FROM workflow_documents wd JOIN documents d ON d.id = wd.document_id
                        WHERE wd.workflow_id = %s AND d.extraction_status <> 'completed' LIMIT 1
                    """, (workflow_id,))
                    if cursor.fetchone():
                        raise HTTPException(409, "Attached documents must finish extraction before execution")
                    cursor.execute("SELECT status, result_reference FROM workflow_steps WHERE workflow_id = %s", (workflow_id,))
                    steps = cursor.fetchall()
                    if not steps:
                        raise HTTPException(409, "A workflow plan is required")
                    if target == "COMPLETED" and any(s["status"] != "COMPLETED" or not s["result_reference"] for s in steps):
                        raise HTTPException(409, "Confirmed results for every step are required")
            cursor.execute("""
                UPDATE workflow_runs SET status = %s, version = version + 1,
                    updated_at = clock_timestamp() WHERE id = %s RETURNING *
            """, (target, workflow_id))
            return serialize(cursor, cursor.fetchone())


def cancel_workflow(settings, workflow_id, user, expected_version):
    if user["role"] == Role.AUDITOR:
        raise HTTPException(403, "Auditors have read-only access")
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            row = load(cursor, workflow_id, user, lock=True)
            check_version(row, expected_version)
            if "CANCELLED" not in TRANSITIONS[row["status"]]:
                raise HTTPException(409, "Terminal workflows cannot be cancelled")
            cursor.execute("SELECT 1 FROM tool_calls WHERE workflow_id=%s AND approval_id IS NOT NULL AND status='pending' LIMIT 1", (workflow_id,))
            if cursor.fetchone():
                raise HTTPException(409, "An external write is in progress; wait for its recorded outcome")
            cursor.execute("UPDATE approvals SET status='invalidated' WHERE workflow_id=%s AND status IN ('pending','approved') RETURNING id", (workflow_id,))
            from backend.services.approval_service import event
            for approval in cursor.fetchall():
                event(cursor, approval["id"], user["user_id"], "invalidated", "Workflow cancelled before execution")
            cursor.execute("""
                UPDATE workflow_runs SET status = 'CANCELLED', version = version + 1,
                    updated_at = clock_timestamp() WHERE id = %s RETURNING *
            """, (workflow_id,))
            updated = dict(cursor.fetchone())
            if row["managed_kind"]:
                from backend.services.business_service import close_case
                close_case(cursor, workflow_id, "cancelled")
            return serialize(cursor, updated)
