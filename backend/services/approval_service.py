"""Snapshot-bound, independent review and one-use execution authorization."""

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from psycopg2.extras import RealDictCursor

from backend.api.schemas import Role
from backend.core.config import get_settings
from backend.db.connection import connection
from backend.integrations.client import checked_json
from backend.integrations.errors import IntegrationError
from backend.risk.policy import POLICY_VERSION, ENABLED_WRITES, classify, reviewers, needs_review
from backend.services.workflow_service import load, check_version, fingerprint


def event(cursor, approval_id, actor, name, reason):
    cursor.execute("INSERT INTO approval_events (id,approval_id,actor_user_id,event,reason) VALUES (%s,%s,%s,%s,%s)",
                   (str(uuid4()), str(approval_id), actor, name, reason))


def action_snapshot(cursor, workflow, settings):
    """No caller-selected risk, identity, action parameters or evidence are trusted."""
    from backend.services.business_service import CLAIM_READS, expected_arguments
    workflow_id = str(workflow["id"])
    cursor.execute("SELECT * FROM workflow_cases WHERE workflow_id=%s", (workflow_id,))
    case = cursor.fetchone()
    if not case or case["phase"] not in {"awaiting_approval", "collecting"}:
        raise IntegrationError("MCP_WRITE_BLOCKED")
    cursor.execute("SELECT * FROM workflow_steps WHERE workflow_id=%s AND status<>'COMPLETED' ORDER BY position LIMIT 1", (workflow_id,))
    step = cursor.fetchone()
    if not step or step["status"] != "CREATED" or ENABLED_WRITES.get(step["action"]) != case["kind"]:
        raise IntegrationError("MCP_WRITE_BLOCKED")
    operation = step["action"]
    args = expected_arguments(cursor, workflow_id, operation)
    if args is None or any(value is None for value in args.values()):
        raise HTTPException(409, "Required action inputs are missing")
    cursor.execute("""SELECT e.operation,e.tool_call_id,e.facts,t.status,t.request_fingerprint,s.result_reference
        FROM workflow_case_evidence e JOIN tool_calls t ON t.id=e.tool_call_id
        JOIN workflow_steps s ON s.id=t.workflow_step_id
        WHERE e.workflow_id=%s AND t.workflow_id=%s AND s.status='COMPLETED'""", (workflow_id, workflow_id))
    evidence = {row["operation"]: dict(row) for row in cursor.fetchall()}
    required = {"ehr.search_patients"} if case["kind"] == "onboarding" else set(CLAIM_READS)
    if not required <= set(evidence):
        raise HTTPException(409, "Required authoritative evidence is missing")
    for op in required:
        proof = evidence[op]
        expected_args = expected_arguments(cursor, workflow_id, op)
        if (proof["status"] != "succeeded" or proof["result_reference"] != f"tool-call:{proof['tool_call_id']}"
                or proof["request_fingerprint"] != hashlib.sha256(checked_json(expected_args, 65536, "MCP_INPUT_INVALID")).hexdigest()):
            raise HTTPException(409, "Evidence no longer matches the planned action")
    cursor.execute("SELECT d.id,d.extraction_status AS status,d.sha256,d.extraction_version FROM documents d JOIN workflow_documents wd ON wd.document_id=d.id WHERE wd.workflow_id=%s ORDER BY d.id", (workflow_id,))
    documents = [dict(row) for row in cursor.fetchall()]
    if any(doc["status"] != "completed" for doc in documents):
        raise HTTPException(409, "Document extraction is incomplete")
    graph = []
    if case["kind"] == "onboarding":
        count = evidence["ehr.search_patients"]["facts"].get("matching_records")
        if type(count) is not int or count != 0:
            raise HTTPException(409, "Onboarding requires a confirmed negative duplicate check")
        cursor.execute("SELECT status FROM patient_profiles WHERE id=%s", (str(case["entity_id"]),))
        if cursor.fetchone()["status"] != "draft":
            raise HTTPException(409, "Patient is no longer a draft")
    else:
        data = case["intake"]
        cursor.execute("SELECT status FROM claims WHERE id=%s", (str(case["entity_id"]),))
        if cursor.fetchone()["status"] != "prepared":
            raise HTTPException(409, "Claim is not prepared")
        for op in required:
            facts = evidence[op]["facts"]
            for field, expected in (("patient_id",data["external_patient_id"]), ("procedure_code",data["procedure_code"]),
                                    ("policy_id",data["policy_id"]), ("currency",data["currency"])):
                if field in facts and facts[field] != expected:
                    raise HTTPException(409, "Claim evidence has conflicting bindings")
            if op != "billing.get_procedure_charge" and "amount" in facts and Decimal(facts["amount"]) != Decimal(data["amount"]):
                raise HTTPException(409, "Claim amount evidence changed")
        for op, flag in (("billing.validate_claim_amount","valid"), ("insurance.get_policy","active"), ("insurance.validate_coverage","covered")):
            if evidence[op]["facts"].get(flag) is not True:
                raise HTTPException(409, "Claim validation evidence is not positive")
        if data.get("use_graph"):
            from backend.services.graph_service import evidence_ready
            if not evidence_ready(cursor, workflow_id):
                raise HTTPException(409, "Required graph evidence is missing or unresolved")
            cursor.execute("""SELECT q.id,q.result_reference,q.graph_revision,q.item_count,q.unresolved_dependencies
                FROM workflow_graph_queries q JOIN workflow_steps s ON s.id=q.workflow_step_id
                WHERE q.workflow_id=%s AND s.result_reference='graph-query:' || q.id::text ORDER BY q.operation""", (workflow_id,))
            graph = [dict(row) for row in cursor.fetchall()]
    # UUIDs arrive as strings with the project's psycopg2 configuration.
    snapshot = {"workflow_id": workflow_id, "step_id": str(step["id"]), "operation": operation,
                "subject": case["subject_user_id"], "entity_id": str(case["entity_id"]), "intake": case["intake"],
                "arguments": args, "evidence": evidence, "documents": documents, "graph": graph,
                "policy": POLICY_VERSION, "medium_review": settings.medium_risk_review, "risk": classify(operation)}
    return dict(step), args, fingerprint(snapshot)


def resume(cursor, workflow_id):
    cursor.execute("UPDATE workflow_runs SET status='EXECUTING',version=version+1,updated_at=clock_timestamp() WHERE id=%s", (workflow_id,))
    cursor.execute("UPDATE workflow_cases SET phase='collecting',updated_at=clock_timestamp() WHERE workflow_id=%s", (workflow_id,))
    # The claim remains prepared while its approved submission is executing.


def decider_valid(cursor, approval, settings):
    if approval["decision_mode"] == "automatic":
        return approval["decided_by"] == "system:risk-policy" and not needs_review(approval["operation"], settings)
    cursor.execute("SELECT role,is_active FROM public.users WHERE user_id::text=%s", (approval["decided_by"],))
    actor = cursor.fetchone()
    return bool(actor and actor["is_active"] and actor["role"] in reviewers(approval["operation"]))


def enqueue(cursor, workflow_id, actor, settings=None, reason="Review required by server risk policy"):
    settings = settings or get_settings()
    cursor.execute("SELECT * FROM workflow_runs WHERE id=%s FOR UPDATE", (workflow_id,))
    workflow = dict(cursor.fetchone())
    step, _, digest = action_snapshot(cursor, workflow, settings)
    cursor.execute("SELECT * FROM approvals WHERE workflow_step_id=%s AND status IN ('pending','approved') FOR UPDATE", (str(step["id"]),))
    old = cursor.fetchone()
    if old:
        fresh = old["scope_fingerprint"] == digest and old["expires_at"] > datetime.now(timezone.utc)
        if fresh and (old["status"] == "pending" or decider_valid(cursor, old, settings)):
            return dict(old)
        cursor.execute("UPDATE approvals SET status='invalidated' WHERE id=%s", (str(old["id"]),))
        event(cursor, old["id"], actor, "invalidated", "Replaced after approval scope, expiry, or reviewer changed")
    approval_id = str(uuid4())
    automatic = not needs_review(step["action"], settings)
    cursor.execute("""INSERT INTO approvals
        (id,workflow_id,workflow_step_id,operation,risk,policy_version,scope_fingerprint,requested_by,request_reason,expires_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""", (approval_id, workflow_id, str(step["id"]), step["action"],
            classify(step["action"]), POLICY_VERSION, digest, actor, reason,
            datetime.now(timezone.utc)+timedelta(minutes=settings.approval_ttl_minutes)))
    event(cursor, approval_id, actor, "requested", reason)
    if automatic:
        cursor.execute("""UPDATE approvals SET status='approved',decision_mode='automatic',decided_by='system:risk-policy',
            decided_at=clock_timestamp(),decision_reason='Automatic execution allowed by medium-risk policy' WHERE id=%s""", (approval_id,))
        event(cursor, approval_id, "system:risk-policy", "automatic", "Automatic execution allowed by medium-risk policy")
        resume(cursor, workflow_id)
    else:
        cursor.execute("UPDATE workflow_runs SET status='WAITING_FOR_APPROVAL',version=version+1,updated_at=clock_timestamp() WHERE id=%s", (workflow_id,))
        cursor.execute("UPDATE workflow_cases SET phase='awaiting_approval' WHERE workflow_id=%s", (workflow_id,))
    cursor.execute("SELECT * FROM approvals WHERE id=%s", (approval_id,))
    return dict(cursor.fetchone())


def request_approval(settings, workflow_id, user, body):
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            workflow = load(cursor, workflow_id, user, lock=True)
            check_version(workflow, body.expected_version)
            if user["role"] == Role.AUDITOR or workflow["status"] not in {"WAITING_FOR_APPROVAL", "EXECUTING"}:
                raise HTTPException(403, "Approval request is not allowed")
            return enqueue(cursor, workflow_id, user["user_id"], settings, body.reason)


def decide(settings, workflow_id, user, body, approve):
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            workflow = load(cursor, workflow_id, user, lock=True)
            check_version(workflow, body.expected_version)
            cursor.execute("SELECT * FROM approvals WHERE id=%s AND workflow_id=%s FOR UPDATE", (str(body.approval_id), workflow_id))
            approval = cursor.fetchone()
            if not approval:
                raise HTTPException(404, "Approval not found")
            if user["role"] not in reviewers(approval["operation"]) or user["user_id"] in {
                approval["requested_by"], workflow["owner_user_id"], workflow["subject_user_id"]}:
                raise HTTPException(403, "Independent authorized reviewer required")
            if approval["status"] != "pending" or workflow["status"] != "WAITING_FOR_APPROVAL":
                raise HTTPException(409, "Approval is no longer pending")
            if approve:
                _, _, digest = action_snapshot(cursor, workflow, settings)
                if approval["expires_at"] <= datetime.now(timezone.utc) or digest != approval["scope_fingerprint"]:
                    raise IntegrationError("APPROVAL_STALE")
            cursor.execute("""UPDATE approvals SET status=%s,decision_mode='human',decided_by=%s,
                decided_at=clock_timestamp(),decision_reason=%s WHERE id=%s RETURNING *""",
                ("approved" if approve else "rejected", user["user_id"], body.reason, str(approval["id"])))
            result = dict(cursor.fetchone())
            event(cursor, approval["id"], user["user_id"], "approved" if approve else "rejected", body.reason)
            if approve:
                resume(cursor, workflow_id)
            else:
                from backend.services.business_service import close_case
                close_case(cursor, workflow_id, "rejected")
                cursor.execute("UPDATE workflow_runs SET version=version+1 WHERE id=%s", (workflow_id,))
            return result


def authorized(cursor, workflow, step_id, settings):
    step, args, digest = action_snapshot(cursor, workflow, settings)
    if str(step["id"]) != str(step_id):
        raise IntegrationError("APPROVAL_REQUIRED")
    cursor.execute("SELECT * FROM approvals WHERE workflow_step_id=%s AND status='approved' FOR UPDATE", (str(step_id),))
    approval = cursor.fetchone()
    if not approval:
        raise IntegrationError("APPROVAL_REQUIRED")
    if (approval["expires_at"] <= datetime.now(timezone.utc) or approval["scope_fingerprint"] != digest
            or not decider_valid(cursor, approval, settings)):
        raise IntegrationError("APPROVAL_STALE")
    return dict(approval), args


def consume(cursor, approval, call_id, actor):
    cursor.execute("""UPDATE approvals SET status='consumed',consumed_by=%s,consumed_at=clock_timestamp(),consumed_call_id=%s
        WHERE id=%s AND status='approved'""", (actor, call_id, str(approval["id"])))
    if cursor.rowcount != 1:
        raise IntegrationError("APPROVAL_REQUIRED")
    event(cursor, approval["id"], actor, "consumed", "Approved action reserved for one external execution")


def list_workflow(settings, workflow_id, user):
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            load(cursor, workflow_id, user)
            cursor.execute("SELECT * FROM approvals WHERE workflow_id=%s ORDER BY requested_at,id", (workflow_id,))
            return [dict(row) for row in cursor.fetchall()]


def queue(settings, user, limit, offset):
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            operations = [op for op in ENABLED_WRITES if user["role"] in reviewers(op)]
            cursor.execute("""SELECT * FROM approvals WHERE status='pending' AND operation=ANY(%s)
                ORDER BY requested_at,id LIMIT %s OFFSET %s""", (operations,limit,offset))
            return [dict(row) for row in cursor.fetchall()]


def review(settings, workflow_id, approval_id, user):
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            workflow = load(cursor, workflow_id, user, lock=True)
            cursor.execute("SELECT * FROM approvals WHERE id=%s AND workflow_id=%s", (str(approval_id), workflow_id))
            approval = cursor.fetchone()
            if not approval:
                raise HTTPException(404, "Approval not found")
            if user["role"] not in reviewers(approval["operation"]):
                raise HTTPException(403, "Reviewer access required")
            _, args, digest = action_snapshot(cursor, workflow, settings)
            cursor.execute("SELECT * FROM approval_events WHERE approval_id=%s ORDER BY created_at,id", (str(approval_id),))
            return {"arguments": args, "scope_matches": digest == approval["scope_fingerprint"], "events": [dict(row) for row in cursor.fetchall()]}
