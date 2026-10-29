"""Deterministic onboarding/claim services; no agent or clinical reasoning."""

from uuid import uuid4

from fastapi import HTTPException
from psycopg2.extras import Json, RealDictCursor

from backend.api.business_schemas import CaseStatus, PatientProfile, ClaimRecord, AppointmentRecord
from backend.api.schemas import Role, WorkflowCreate
from backend.db.connection import connection
from backend.services.document_service import link_documents
from backend.services.workflow_service import insert_workflow, serialize, lock_key, load, check_version

CLINICAL = {Role.DOCTOR, Role.ADMIN}
STAFF = CLINICAL | {Role.RECEPTIONIST}
CLAIM_READS = ("ehr.get_patient", "billing.get_procedure_charge", "billing.validate_claim_amount",
               "insurance.get_policy", "insurance.validate_coverage")


def require_subject(user, owner_id, *, clinical=False):
    allowed = CLINICAL if clinical else STAFF
    if user["role"] not in allowed and user["user_id"] != owner_id:
        raise HTTPException(404, "Patient resource not found")
    if user["role"] == Role.AUDITOR:
        raise HTTPException(403, "Auditors may inspect workflow status only")


def get_profile(cursor, profile_id, user, *, active=False, clinical=False):
    cursor.execute("SELECT * FROM patient_profiles WHERE id = %s", (str(profile_id),))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(404, "Patient profile not found")
    require_subject(user, row["owner_user_id"], clinical=clinical)
    if active and row["status"] != "active":
        raise HTTPException(409, "Patient profile must be confirmed against the EHR first")
    return dict(row)


def get_case(cursor, workflow_id, user, *, lock=False):
    workflow = load(cursor, workflow_id, user, lock=lock)
    cursor.execute("SELECT * FROM workflow_cases WHERE workflow_id = %s", (workflow_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(404, "Managed workflow not found")
    return workflow, dict(row)


def status(cursor, workflow, case):
    cursor.execute("SELECT action, status FROM workflow_steps WHERE workflow_id = %s ORDER BY position", (str(workflow["id"]),))
    steps = cursor.fetchall()
    next_action = None if any(s["status"] == "EXECUTING" for s in steps) else next(
        (s["action"] for s in steps if s["status"] == "CREATED"), None)
    if case["phase"] in {"confirmed", "cancelled", "failed"}:
        next_action = None
    return CaseStatus(workflow=serialize(cursor, workflow), kind=case["kind"], subject_user_id=case["subject_user_id"],
                      entity_id=case["entity_id"], phase=case["phase"], missing_fields=case["missing_fields"], next_action=next_action)


def missing_data(cursor, workflow_id, kind, intake):
    required = ("full_name", "date_of_birth", "phone") if kind == "onboarding" else ("policy_id", "procedure_code", "amount")
    missing = [field for field in required if intake.get(field) is None]
    cursor.execute("""SELECT d.extraction_status FROM workflow_documents wd JOIN documents d ON d.id = wd.document_id
        WHERE wd.workflow_id = %s""", (workflow_id,))
    if any(d["extraction_status"] != "completed" for d in cursor.fetchall()):
        missing.append("document_extraction")
    return missing


def set_phase(cursor, workflow_id, phase, missing=()):
    state = {"waiting_data": "WAITING_FOR_DATA", "collecting": "EXECUTING", "awaiting_approval": "WAITING_FOR_APPROVAL",
             "confirmed": "COMPLETED", "failed": "FAILED", "cancelled": "CANCELLED"}[phase]
    cursor.execute("UPDATE workflow_cases SET phase = %s, missing_fields = %s, updated_at = clock_timestamp() WHERE workflow_id = %s",
                   (phase, Json(list(missing)), workflow_id))
    cursor.execute("UPDATE workflow_runs SET status = %s, updated_at = clock_timestamp() WHERE id = %s", (state, workflow_id))
    cursor.execute("""UPDATE claims SET status = %s, updated_at = clock_timestamp() WHERE workflow_id = %s""",
                   ({"waiting_data": "waiting_for_data", "collecting": "preparing", "awaiting_approval": "prepared",
                     "confirmed": "submitted", "failed": "failed", "cancelled": "cancelled"}[phase], workflow_id))


def close_case(cursor, workflow_id, phase):
    set_phase(cursor, workflow_id, phase)
    cursor.execute("UPDATE patient_profiles SET status = %s, updated_at = clock_timestamp() WHERE onboarding_workflow_id = %s AND status = 'draft'",
                   ("cancelled" if phase == "cancelled" else "failed", workflow_id))


def add_step(cursor, workflow_id, action, position):
    cursor.execute("INSERT INTO workflow_steps (id,workflow_id,position,action,risk) VALUES (%s,%s,%s,%s,%s)",
                   (str(uuid4()), workflow_id, position, action, "high" if action in {"ehr.create_patient", "insurance.submit_claim"} else "low"))


def start(settings, user, body, key, kind):
    if user["role"] == Role.AUDITOR or (kind == "insurance_claim" and user["role"] == Role.RECEPTIONIST):
        raise HTTPException(403, "Insufficient case permissions")
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            if kind == "onboarding":
                subject = body.patient_user_id or user["user_id"]
                require_subject(user, subject)
                cursor.execute("SELECT email FROM public.users WHERE user_id::text = %s AND role = 'patient' AND is_active = TRUE", (subject,))
                account = cursor.fetchone()
                if not account:
                    raise HTTPException(422, "Onboarding requires an active patient account")
                intake = body.model_dump(mode="json", exclude={"patient_user_id", "request", "document_ids"})
                intake["contact_email"] = account["email"].lower()
            else:
                profile = get_profile(cursor, body.patient_profile_id, user, active=True, clinical=True)
                subject = profile["owner_user_id"]
                intake = body.model_dump(mode="json", exclude={"patient_profile_id", "request", "document_ids"})
                intake["patient_profile_id"] = str(profile["id"])
                intake["external_patient_id"] = profile["external_patient_id"]
                intake["patient_email"] = profile["contact_email"]
            payload = body.model_dump(mode="json")
            if kind == "insurance_claim" and not body.use_graph:
                payload.pop("use_graph")  # Preserve Part 5 idempotency fingerprints.
            created, replayed = insert_workflow(cursor, WorkflowCreate(workflow_type=kind, request=body.request,
                document_ids=body.document_ids or []), user, key, payload={"kind": kind, "body": payload})
            workflow_id = str(created["id"])
            if replayed:
                workflow, case = get_case(cursor, workflow_id, user)
                return status(cursor, workflow, case), True
            lock_key(cursor, f"onboarding-subject:{subject}" if kind == "onboarding" else f"claim-start:{workflow_id}")
            entity_id = str(uuid4())
            if kind == "onboarding":
                cursor.execute("SELECT 1 FROM patient_profiles WHERE owner_user_id = %s AND status = 'active'", (subject,))
                if cursor.fetchone():
                    raise HTTPException(409, "Patient already has a confirmed profile")
                cursor.execute("SELECT 1 FROM workflow_cases WHERE subject_user_id = %s AND kind = 'onboarding' AND phase IN ('waiting_data','collecting','awaiting_approval')", (subject,))
                if cursor.fetchone():
                    raise HTTPException(409, "Patient already has an active onboarding workflow")
                cursor.execute("""INSERT INTO patient_profiles (id,owner_user_id,onboarding_workflow_id,full_name,date_of_birth,contact_email,phone)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)""", (entity_id, subject, workflow_id, intake["full_name"], intake["date_of_birth"], intake["contact_email"], intake["phone"]))
            else:
                cursor.execute("""INSERT INTO claims (id,workflow_id,patient_profile_id,owner_user_id,policy_id,procedure_code,amount,currency,status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'waiting_for_data')""", (entity_id, workflow_id, intake["patient_profile_id"], subject,
                        intake["policy_id"], intake["procedure_code"], intake["amount"], intake["currency"]))
            cursor.execute("UPDATE workflow_runs SET status='PLANNING', managed_kind = %s, subject_user_id = %s WHERE id = %s", (kind, subject, workflow_id))
            cursor.execute("""INSERT INTO workflow_cases (workflow_id,kind,subject_user_id,entity_id,intake,phase)
                VALUES (%s,%s,%s,%s,%s,'waiting_data')""", (workflow_id, kind, subject, entity_id, Json(intake)))
            actions = ("ehr.search_patients",) if kind == "onboarding" else (*CLAIM_READS, "insurance.submit_claim")
            if kind == "insurance_claim" and body.use_graph:
                from backend.graph.contracts import GRAPH_OPERATIONS
                actions = (*CLAIM_READS, *(f"graph.{op}" for op in GRAPH_OPERATIONS), "insurance.submit_claim")
            for pos, action in enumerate(actions):
                add_step(cursor, workflow_id, action, pos)
            missing = missing_data(cursor, workflow_id, kind, intake)
            set_phase(cursor, workflow_id, "waiting_data" if missing else "collecting", missing)
            workflow, case = get_case(cursor, workflow_id, user)
            return status(cursor, workflow, case), False


def get_status(settings, workflow_id, user):
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            return status(cursor, *get_case(cursor, workflow_id, user, lock=True))


def update_intake(settings, workflow_id, user, body, kind):
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            workflow, case = get_case(cursor, workflow_id, user, lock=True)
            require_subject(user, case["subject_user_id"], clinical=kind == "insurance_claim")
            if case["kind"] != kind:
                raise HTTPException(409, "Workflow kind does not match")
            check_version(workflow, body.expected_version)
            if case["phase"] != "waiting_data":
                raise HTTPException(409, "Intake may only be corrected while waiting for data")
            cursor.execute("SELECT 1 FROM tool_calls WHERE workflow_id = %s AND (status <> 'succeeded' OR tool IN ('create_patient','submit_claim')) LIMIT 1", (workflow_id,))
            if cursor.fetchone():
                raise HTTPException(409, "Uncertain or mutating operations require reconciliation")
            cursor.execute("SELECT 1 FROM workflow_graph_queries WHERE workflow_id=%s AND status<>'succeeded' LIMIT 1", (workflow_id,))
            if cursor.fetchone():
                raise HTTPException(409, "Unfinished or failed graph queries require reconciliation")
            intake = {**case["intake"], **body.model_dump(mode="json", exclude_unset=True, exclude={"expected_version", "document_ids"})}
            if body.document_ids is not None:
                cursor.execute("DELETE FROM workflow_documents WHERE workflow_id = %s", (workflow_id,))
                link_documents(cursor, workflow_id, body.document_ids, user)
                cursor.execute("UPDATE workflow_runs SET document_ids = %s WHERE id = %s", (Json([str(d) for d in body.document_ids]), workflow_id))
            cursor.execute("UPDATE workflow_cases SET intake = %s WHERE workflow_id = %s", (Json(intake), workflow_id))
            cursor.execute("DELETE FROM workflow_case_evidence WHERE workflow_id = %s", (workflow_id,))
            cursor.execute("UPDATE workflow_steps SET status = 'CREATED', result_reference = NULL WHERE workflow_id = %s", (workflow_id,))
            if kind == "onboarding":
                cursor.execute("UPDATE patient_profiles SET full_name=%s,date_of_birth=%s,phone=%s,updated_at=clock_timestamp() WHERE id=%s AND status='draft'",
                               (intake["full_name"], intake["date_of_birth"], intake["phone"], str(case["entity_id"])))
            else:
                cursor.execute("UPDATE claims SET policy_id=%s,procedure_code=%s,amount=%s,currency=%s WHERE id=%s",
                               (intake["policy_id"], intake["procedure_code"], intake["amount"], intake["currency"], str(case["entity_id"])))
            missing = missing_data(cursor, workflow_id, kind, intake)
            set_phase(cursor, workflow_id, "waiting_data" if missing else "collecting", missing)
            cursor.execute("UPDATE workflow_runs SET version = version + 1 WHERE id = %s", (workflow_id,))
            return status(cursor, *get_case(cursor, workflow_id, user))


def expected_arguments(cursor, workflow_id, operation):
    cursor.execute("SELECT * FROM workflow_cases WHERE workflow_id = %s", (workflow_id,))
    case = cursor.fetchone()
    if not case:
        return None
    data = case["intake"]
    if case["kind"] == "onboarding":
        if operation == "ehr.search_patients":
            return {"query": data["contact_email"]}
        if operation == "ehr.create_patient":
            return {key: data[key] for key in ("full_name", "date_of_birth", "contact_email", "phone")}
    common = {"patient_id": data.get("external_patient_id")}
    amount = {"amount": data.get("amount"), "currency": data.get("currency")}
    procedure = {"procedure_code": data.get("procedure_code")}
    policy = {"policy_id": data.get("policy_id")}
    return {"ehr.get_patient": common, "billing.get_procedure_charge": procedure,
            "billing.validate_claim_amount": {**common, **procedure, **amount},
            "insurance.get_policy": {**common, **policy},
            "insurance.validate_coverage": {**common, **policy, **procedure, **amount},
            "insurance.submit_claim": {**common, **policy, **procedure, **amount}}.get(operation)


def next_call(settings, workflow_id, user, expected_version):
    from backend.services.tool_call_service import ToolCallInput
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            workflow, case = get_case(cursor, workflow_id, user)
            check_version(workflow, expected_version)
            if case["phase"] == "awaiting_approval":
                from backend.integrations.errors import IntegrationError
                raise IntegrationError("MCP_WRITE_BLOCKED")
            if case["phase"] != "collecting":
                raise HTTPException(409, "Workflow is not ready to advance")
            cursor.execute("SELECT id,action FROM workflow_steps WHERE workflow_id = %s AND status <> 'COMPLETED' ORDER BY position LIMIT 1", (workflow_id,))
            step = cursor.fetchone()
            if not step:
                raise HTTPException(409, "No executable step remains")
            if step["action"].startswith("graph."):
                from backend.graph.contracts import GraphQueryInput
                return GraphQueryInput(expected_version=expected_version, step_id=step["id"],
                    patient_profile_id=case["intake"]["patient_profile_id"], operation=step["action"].split(".", 1)[1])
            args = expected_arguments(cursor, workflow_id, step["action"])
            body = ToolCallInput(expected_version=expected_version, step_id=step["id"], operation=step["action"], arguments=args)
    return body


async def advance(settings, client, workflow_id, user, expected_version, *, graph_client=None):
    from starlette.concurrency import run_in_threadpool
    from backend.services.tool_call_service import execute
    body = await run_in_threadpool(next_call, settings, workflow_id, user, expected_version)
    from backend.graph.contracts import GraphQueryInput
    if isinstance(body, GraphQueryInput):
        from backend.graph.client import GraphClient
        from backend.services.graph_service import execute as execute_graph
        await execute_graph(settings, graph_client or GraphClient(), workflow_id, user, body)
    else:
        await execute(settings, client, workflow_id, user, body)
    return await run_in_threadpool(get_status, settings, workflow_id, user)


def read_entity(settings, table, entity_id, user):
    model = {"patient_profiles": PatientProfile, "claims": ClaimRecord, "appointments": AppointmentRecord}[table]
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(f"SELECT * FROM {table} WHERE id = %s", (str(entity_id),))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(404, "Resource not found")
            require_subject(user, row["owner_user_id"], clinical=table == "claims")
            return model(**dict(row))


def evidence(settings, workflow_id, user):
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            _, case = get_case(cursor, workflow_id, user)
            require_subject(user, case["subject_user_id"], clinical=case["kind"] == "insurance_claim")
            cursor.execute("SELECT operation,tool_call_id,facts FROM workflow_case_evidence WHERE workflow_id=%s ORDER BY operation", (workflow_id,))
            return [dict(row) for row in cursor.fetchall()]


def create_appointment(settings, body, user):
    if user["role"] == Role.AUDITOR:
        raise HTTPException(403, "Auditors have read-only access")
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            profile = get_profile(cursor, body.patient_profile_id, user, active=True)
            cursor.execute("""INSERT INTO appointments (id,patient_profile_id,owner_user_id,doctor_reference,starts_at,duration_minutes)
                VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""", (str(uuid4()), str(body.patient_profile_id), profile["owner_user_id"],
                    body.doctor_reference, body.starts_at, body.duration_minutes))
            return AppointmentRecord(**dict(cursor.fetchone()))
