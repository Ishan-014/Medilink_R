import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.application import create_app
from backend.db.connection import connection
from backend.integrations.client import HospitalClient
from backend.integrations.client import checked_json
from backend.services.business_service import expected_arguments
from backend.services.tool_call_service import finalize
from backend.services.workflow_service import cancel_workflow
from backend.services.business_evidence import PatientSource

ONBOARD = {"full_name": "Synthetic Patient", "date_of_birth": "2000-01-01", "phone": "+919876543210"}
PATIENT = {"id": "EHR-SYNTHETIC", "name": "Synthetic Patient", "date_of_birth": "2000-01-01",
           "email": "patient@example.com", "clinical_notes": "Do not copy clinical notes"}


def test_contradictory_patient_identifiers_are_not_accepted_as_evidence():
    with pytest.raises(ValidationError):
        PatientSource.model_validate({**PATIENT, "patient_id": "ANOTHER-PATIENT"})


class HospitalSession:
    def __init__(self):
        self.calls = []
        self.matches = [PATIENT]
        self.mode = "ok"
        self.covered = True
        self.active = True
        self.valid_amount = True
        self.before_call = None

    async def initialize(self):
        pass

    async def list_tools(self):
        names = ("search_patients", "get_patient", "get_procedure_charge", "validate_claim_amount", "get_policy", "validate_coverage")
        return SimpleNamespace(nextCursor=None, tools=[SimpleNamespace(name=name, inputSchema={"type": "object"}, outputSchema={"type": "object"}) for name in names])

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.before_call:
            self.before_call()
        if self.mode == "timeout":
            await asyncio.sleep(1)
        if name == "search_patients":
            result = {"result": self.matches}
        elif name == "get_patient":
            result = {**PATIENT, "id": "WRONG-PATIENT" if self.mode == "wrong_patient" else arguments["patient_id"]}
        elif name == "get_procedure_charge":
            result = {"procedure_code": arguments["procedure_code"], "amount": "125.50", "currency": "INR"}
        elif name == "validate_claim_amount":
            result = {**arguments, "valid": self.valid_amount}
        elif name == "get_policy":
            result = {**arguments, "active": self.active, "currency": "INR"}
        else:
            result = {**arguments, "covered": self.covered}
        if self.mode == "missing_evidence" and name == "validate_coverage":
            result = {"patient_id": arguments["patient_id"]}
        if self.mode == "wrong_amount" and name == "validate_claim_amount":
            result["amount"] = "999.99"
        return SimpleNamespace(isError=False, structuredContent=result)


class Transport:
    def __init__(self, peer):
        self.peer = peer

    @asynccontextmanager
    async def session(self, config):
        yield self.peer


@pytest.fixture
def business(api_env, tmp_path):
    path = tmp_path / "business-mcp.json"
    path.write_text(json.dumps({"servers": {name: {"transport": "streamable_http", "url": "http://127.0.0.1:8102/mcp", "timeout_seconds": 0.1}
                                             for name in ("ehr", "billing", "insurance")}}))
    api_env.settings = replace(api_env.settings, mcp_config_file=path)
    api_env.client.app.state.settings = api_env.settings
    peer = HospitalSession()
    api_env.client.app.state.hospital_client = HospitalClient(Transport(peer))
    return api_env, peer


def start_onboarding(env, body=None, *, role="patient", key=None):
    headers = env.headers(role)
    if key:
        headers["Idempotency-Key"] = key
    return env.client.post("/api/onboarding", headers=headers, json=ONBOARD if body is None else body)


def advance(env, case, role="doctor"):
    return env.client.post(f"/api/workflows/{case['workflow']['id']}/advance", headers=env.headers(role),
                           json={"expected_version": case["workflow"]["version"]})


def confirmed_profile(env):
    started = start_onboarding(env)
    assert started.status_code == 201
    confirmed = advance(env, started.json()["data"])
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["phase"] == "confirmed"
    return confirmed.json()["data"]["entity_id"]


def start_claim(env, profile, body=None):
    return env.client.post("/api/claims", headers=env.headers(), json={
        "patient_profile_id": profile, "policy_id": "POL-SYNTHETIC", "procedure_code": "PROC-SYNTHETIC", "amount": "125.50", "currency": "INR",
        **(body or {}),
    })


def prepare_claim(env, case):
    for _ in range(5):
        response = advance(env, case)
        assert response.status_code == 200
        case = response.json()["data"]
        if case["phase"] != "collecting":
            break
    return case


def test_onboarding_requires_confirmed_ehr_success_and_persists(business):
    env, peer = business
    started = start_onboarding(env).json()["data"]
    profile = env.client.get(f"/api/patients/{started['entity_id']}", headers=env.headers()).json()["data"]
    assert profile["status"] == "draft" and profile["external_patient_id"] is None
    completed = advance(env, started).json()["data"]
    assert completed["phase"] == "confirmed" and completed["workflow"]["status"] == "COMPLETED"
    assert [c[0] for c in peer.calls] == ["search_patients"]
    with TestClient(create_app(env.settings)) as restarted:
        profile = restarted.get(f"/api/patients/{started['entity_id']}", headers=env.headers()).json()["data"]
        assert profile["status"] == "active" and profile["external_patient_id"] == "EHR-SYNTHETIC"
        assert profile["confirmation_call_id"]
    evidence = env.client.get(f"/api/workflows/{started['workflow']['id']}/evidence", headers=env.headers()).json()["data"]
    assert evidence[0]["facts"] == {"matching_records": 1}
    assert "clinical_notes" not in json.dumps(profile) + json.dumps(evidence)


def test_new_patient_pauses_before_creation(business):
    env, peer = business
    peer.matches = []
    case = advance(env, start_onboarding(env).json()["data"]).json()["data"]
    assert case["phase"] == "awaiting_approval" and case["workflow"]["status"] == "WAITING_FOR_APPROVAL"
    assert case["next_action"] == "ehr.create_patient"
    assert advance(env, case).status_code == 403
    profile = env.client.get(f"/api/patients/{case['entity_id']}", headers=env.headers()).json()["data"]
    assert profile["status"] == "draft" and profile["confirmation_call_id"] is None
    assert len(peer.calls) == 1


def test_missing_intake_can_be_corrected_without_running_tools(business):
    env, peer = business
    case = start_onboarding(env, {}).json()["data"]
    assert case["phase"] == "waiting_data"
    assert set(case["missing_fields"]) == {"full_name", "date_of_birth", "phone"}
    assert advance(env, case).status_code == 409
    corrected = env.client.patch(f"/api/onboarding/{case['workflow']['id']}", headers=env.headers(),
        json={**ONBOARD, "expected_version": case["workflow"]["version"]})
    assert corrected.status_code == 200 and corrected.json()["data"]["phase"] == "collecting"
    assert peer.calls == []
    assert advance(env, corrected.json()["data"]).json()["data"]["phase"] == "confirmed"


@pytest.mark.parametrize("kind", ["duplicate", "mismatch", "missing_source", "truncated"])
def test_uncertain_patient_match_never_activates_profile(business, kind):
    env, peer = business
    if kind == "duplicate":
        peer.matches = [PATIENT, {**PATIENT, "id": "EHR-OTHER"}]
    elif kind == "mismatch":
        peer.matches = [{**PATIENT, "date_of_birth": "1999-01-01"}]
    elif kind == "truncated":
        peer.matches = [{**PATIENT, "id": f"EHR-{i}", "email": f"other{i}@example.com"} for i in range(100)]
    else:
        peer.matches = [{"id": "EHR-ONLY-ID"}]
    case = advance(env, start_onboarding(env).json()["data"]).json()["data"]
    assert case["phase"] == "waiting_data"
    assert env.client.get(f"/api/patients/{case['entity_id']}", headers=env.headers()).json()["data"]["status"] == "draft"


def test_claim_preparation_gathers_authoritative_evidence_without_submission(business):
    env, peer = business
    profile = confirmed_profile(env)
    case = prepare_claim(env, start_claim(env, profile).json()["data"])
    assert case["phase"] == "awaiting_approval" and case["workflow"]["status"] == "WAITING_FOR_APPROVAL"
    assert case["next_action"] == "insurance.submit_claim"
    claim = env.client.get(f"/api/claims/{case['entity_id']}", headers=env.headers()).json()["data"]
    assert claim["status"] == "prepared" and claim["external_claim_id"] is None and claim["submission_call_id"] is None
    evidence = env.client.get(f"/api/workflows/{case['workflow']['id']}/evidence", headers=env.headers()).json()["data"]
    assert len(evidence) == 5
    assert {e["operation"].split(".")[0] for e in evidence} == {"ehr", "billing", "insurance"}
    assert advance(env, case).status_code == 403
    assert "submit_claim" not in [c[0] for c in peer.calls]


@pytest.mark.parametrize("flag", ["covered", "active", "valid_amount"])
def test_negative_claim_evidence_pauses_instead_of_claiming_success(business, flag):
    env, peer = business
    profile = confirmed_profile(env)
    setattr(peer, flag, False)
    case = prepare_claim(env, start_claim(env, profile).json()["data"])
    assert case["phase"] == "waiting_data" and case["missing_fields"]
    claim = env.client.get(f"/api/claims/{case['entity_id']}", headers=env.headers()).json()["data"]
    assert claim["status"] == "waiting_for_data" and claim["external_claim_id"] is None


@pytest.mark.parametrize("mode", ["wrong_patient", "wrong_amount"])
def test_mismatched_claim_receipts_are_failed_not_persisted_as_success(business, mode):
    env, peer = business
    profile = confirmed_profile(env)
    case = start_claim(env, profile).json()["data"]
    peer.mode = mode
    for _ in range(5):
        response = advance(env, case)
        if response.status_code != 200:
            break
        case = response.json()["data"]
    assert response.status_code == 502 and response.json()["error"]["code"] == "MCP_OUTPUT_INVALID"
    current = env.client.get(f"/api/workflows/{case['workflow']['id']}/case", headers=env.headers()).json()["data"]
    assert current["phase"] == "failed" and current["workflow"]["status"] == "FAILED"
    assert env.client.get(f"/api/claims/{current['entity_id']}", headers=env.headers()).json()["data"]["status"] == "failed"


def test_timeout_never_confirms_a_patient_and_cannot_auto_retry(business):
    env, peer = business
    peer.mode = "timeout"
    case = start_onboarding(env).json()["data"]
    assert advance(env, case).status_code == 504
    assert env.client.get(f"/api/patients/{case['entity_id']}", headers=env.headers()).json()["data"]["external_patient_id"] is None
    assert advance(env, case).status_code == 409
    assert len(peer.calls) == 1


def test_managed_plans_status_and_tool_arguments_cannot_be_forged(business):
    env, peer = business
    case = start_onboarding(env).json()["data"]
    run = case["workflow"]
    base = f"/api/workflows/{run['id']}"
    assert env.client.post(base + "/transitions", headers=env.headers("admin"), json={"expected_version": 0, "status": "COMPLETED"}).status_code == 409
    assert env.client.put(base + "/plan", headers=env.headers("admin"), json={"expected_version": 0, "steps": [{"action": "ehr.get_patient"}]}).status_code == 409
    assert env.client.post(base + "/tool-calls", headers=env.headers("doctor"), json={"expected_version": 0,
        "step_id": run["steps"][0]["id"], "operation": "ehr.search_patients", "arguments": {"query": "another@example.com"}}).status_code == 409
    assert peer.calls == []


def test_staff_started_case_is_visible_to_subject_but_not_another_patient(business):
    env, _ = business
    body = {**ONBOARD, "patient_user_id": env.users["patient"]["user_id"]}
    case = start_onboarding(env, body, role="receptionist").json()["data"]
    base = f"/api/workflows/{case['workflow']['id']}"
    assert env.client.get(base, headers=env.headers()).status_code == 200
    assert env.client.get(base + "/case", headers=env.headers("patient2")).status_code == 404
    assert env.client.get(f"/api/patients/{case['entity_id']}", headers=env.headers("patient2")).status_code == 404
    assert env.client.get(f"/api/patients/{case['entity_id']}", headers=env.headers("auditor")).status_code == 403
    assert advance(env, case, role="receptionist").status_code == 403
    assert len(env.client.get("/api/workflows", headers=env.headers()).json()["data"]) == 1


def test_case_start_is_idempotent_and_conflicting_intake_is_rejected(business):
    env, _ = business
    first = start_onboarding(env, key="onboard-1")
    again = start_onboarding(env, key="onboard-1")
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["data"]["workflow"]["id"] == again.json()["data"]["workflow"]["id"]
    assert start_onboarding(env, {**ONBOARD, "full_name": "Other name"}, key="onboard-1").status_code == 409
    assert start_onboarding(env).status_code == 409


def test_concurrent_onboarding_start_and_advance_are_single_winner(business):
    env, peer = business
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: start_onboarding(env, key="concurrent"), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 201]
    case = responses[0].json()["data"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: advance(env, case), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len(peer.calls) == 1


def test_claim_evidence_correction_restarts_reads_preserving_trace(business):
    env, peer = business
    profile = confirmed_profile(env)
    peer.covered = False
    case = prepare_claim(env, start_claim(env, profile).json()["data"])
    peer.covered = True
    response = env.client.patch(f"/api/claims/{case['workflow']['id']}/intake", headers=env.headers(),
                               json={"expected_version": case["workflow"]["version"]})
    assert response.status_code == 200
    case = prepare_claim(env, response.json()["data"])
    assert case["phase"] == "awaiting_approval"
    with connection(env.settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM tool_calls WHERE workflow_id=%s", (case["workflow"]["id"],))
            assert cursor.fetchone()[0] == 10


def test_appointment_is_a_persistent_draft_not_a_false_booking(business):
    env, _ = business
    profile = confirmed_profile(env)
    starts = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    response = env.client.post("/api/appointments", headers=env.headers(), json={"patient_profile_id": profile,
        "doctor_reference": "DOCTOR-SYNTHETIC", "starts_at": starts, "duration_minutes": 30})
    assert response.status_code == 201 and response.json()["data"]["status"] == "draft"
    appointment_id = response.json()["data"]["id"]
    assert env.client.get(f"/api/appointments/{appointment_id}", headers=env.headers("patient2")).status_code == 404
    assert env.client.get(f"/api/appointments/{appointment_id}", headers=env.headers("receptionist")).status_code == 200
    with TestClient(create_app(env.settings)) as restarted:
        assert restarted.get(f"/api/appointments/{appointment_id}", headers=env.headers()).json()["data"]["status"] == "draft"
    assert env.client.post("/api/appointments", headers=env.headers(), json={"patient_profile_id": profile,
        "doctor_reference": "DOCTOR", "starts_at": "2000-01-01T12:00:00", "status": "booked"}).status_code == 422
    with connection(env.settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE appointments SET starts_at='2000-01-01T00:00:00Z' WHERE id=%s", (appointment_id,))
    assert env.client.get(f"/api/appointments/{appointment_id}", headers=env.headers()).status_code == 200


def test_cancelling_in_flight_onboarding_cannot_activate_profile(business):
    env, peer = business
    case = start_onboarding(env).json()["data"]
    workflow_id = case["workflow"]["id"]
    peer.before_call = lambda: cancel_workflow(env.settings, workflow_id, env.users["patient"], 1)
    result = advance(env, case)
    assert result.status_code == 200 and result.json()["data"]["phase"] == "cancelled"
    profile = env.client.get(f"/api/patients/{case['entity_id']}", headers=env.headers()).json()["data"]
    assert profile["status"] == "cancelled" and profile["confirmation_call_id"] is None


def trusted_pending_write(env, case, operation):
    """Seed an unapproved callback to verify that finalization cannot bypass review."""
    workflow_id = case["workflow"]["id"]
    with connection(env.settings) as conn:
        from psycopg2.extras import RealDictCursor
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            arguments = expected_arguments(cursor, workflow_id, operation)
            cursor.execute("SELECT id FROM workflow_steps WHERE workflow_id=%s AND action=%s", (workflow_id, operation))
            step_id = str(cursor.fetchone()["id"])
            call_id = str(uuid4())
            server, tool = operation.split(".")
            cursor.execute("UPDATE workflow_runs SET status='EXECUTING' WHERE id=%s", (workflow_id,))
            cursor.execute("UPDATE workflow_steps SET status='EXECUTING' WHERE id=%s", (step_id,))
            cursor.execute("""INSERT INTO tool_calls (id,workflow_id,workflow_step_id,actor_user_id,server,tool,request_fingerprint)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""", (call_id, workflow_id, step_id, env.users["doctor"]["user_id"], server, tool,
                    hashlib.sha256(checked_json(arguments, 65536, "MCP_INPUT_INVALID")).hexdigest()))
    return call_id, arguments


def test_unapproved_creation_receipt_cannot_activate_patient(business):
    env, peer = business
    peer.matches = []
    case = advance(env, start_onboarding(env).json()["data"]).json()["data"]
    assert advance(env, case).status_code == 403  # The HTTP write gate remains closed.
    call_id, arguments = trusted_pending_write(env, case, "ehr.create_patient")
    from backend.integrations.errors import IntegrationError
    with pytest.raises(IntegrationError, match="403"):
        finalize(env.settings, call_id, 1, arguments=arguments, result=PATIENT)
    profile = env.client.get(f"/api/patients/{case['entity_id']}", headers=env.headers()).json()["data"]
    assert profile["status"] == "draft" and profile["confirmation_call_id"] is None


def test_unapproved_submission_receipt_cannot_submit_claim(business):
    env, _ = business
    case = prepare_claim(env, start_claim(env, confirmed_profile(env)).json()["data"])
    assert advance(env, case).status_code == 403
    call_id, arguments = trusted_pending_write(env, case, "insurance.submit_claim")
    from backend.integrations.errors import IntegrationError
    with pytest.raises(IntegrationError, match="403"):
        finalize(env.settings, call_id, 1, arguments=arguments, result={**arguments, "covered": True,
            "claim_id": "CLAIM-CONFIRMED-SYNTHETIC", "status": "submitted"})
    claim = env.client.get(f"/api/claims/{case['entity_id']}", headers=env.headers()).json()["data"]
    assert claim["status"] == "prepared" and claim["submission_call_id"] is None
    assert claim["external_claim_id"] is None


def test_onboarding_documents_pause_until_extraction_is_complete(business):
    from backend.api.schemas import ExtractionResult
    from backend.services.document_service import claim_extraction, finish_extraction
    env, peer = business
    document = env.client.post("/api/documents", headers=env.headers(), files={"file": ("intake.txt", b"Synthetic", "text/plain")}).json()["data"]
    case = start_onboarding(env, {**ONBOARD, "document_ids": [document["id"]]}).json()["data"]
    assert case["phase"] == "waiting_data" and "document_extraction" in case["missing_fields"]
    assert peer.calls == []
    job = claim_extraction(env.settings, document["id"])
    finish_extraction(env.settings, job, result=ExtractionResult(text="Synthetic"))
    corrected = env.client.patch(f"/api/onboarding/{case['workflow']['id']}", headers=env.headers(), json={"expected_version": 0}).json()["data"]
    assert corrected["phase"] == "collecting"
    assert advance(env, corrected).json()["data"]["phase"] == "confirmed"


def test_claim_start_pause_permissions_and_client_success_fields(business):
    env, _ = business
    profile = confirmed_profile(env)
    case = start_claim(env, profile, {"amount": None, "policy_id": None}).json()["data"]
    assert case["phase"] == "waiting_data" and set(case["missing_fields"]) == {"amount", "policy_id"}
    assert env.client.get(f"/api/claims/{case['entity_id']}", headers=env.headers("patient2")).status_code == 404
    assert env.client.get(f"/api/claims/{case['entity_id']}", headers=env.headers("receptionist")).status_code == 403
    assert env.client.get(f"/api/workflows/{case['workflow']['id']}/evidence", headers=env.headers("auditor")).status_code == 403
    assert start_claim(env, profile, {"external_claim_id": "fake", "status": "submitted"}).status_code == 422
    assert start_claim(env, profile, {"amount": "0"}).status_code == 422
