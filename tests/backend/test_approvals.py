"""Exercise the real HTTP/SQL authorization boundary with a synthetic MCP peer."""

import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import jwt
import psycopg2
import pytest
from fastapi import HTTPException

from backend.db.connection import connection
from backend.integrations.client import checked_json
from backend.risk.policy import classify
from backend.services import patient_service
from backend.services.workflow_service import cancel_workflow
from test_business_workflows import (business as business, PATIENT, start_onboarding,
    advance, confirmed_profile, start_claim, prepare_claim)

SECRET = "synthetic-action-signing-secret-for-part-seven-tests"


@pytest.fixture
def approvals(business):
    env, peer = business
    env.settings = replace(env.settings, action_signing_secret=SECRET)
    env.client.app.state.settings = env.settings
    old_list = peer.list_tools
    old_call = peer.call_tool
    peer.contexts = []
    peer.write_mode = "ok"

    async def listing():
        tools = (await old_list()).tools
        tools += [SimpleNamespace(name=name,inputSchema={"type":"object"},outputSchema={"type":"object"})
                  for name in ("create_patient","submit_claim")]
        return SimpleNamespace(nextCursor=None,tools=tools)

    async def call(name, arguments, *, meta=None):
        if name not in {"create_patient","submit_claim"}:
            assert meta is None
            return await old_call(name, arguments)
        assert meta is not None
        token = jwt.decode(meta["medilink"]["approval_token"], SECRET, algorithms=["HS256"],
                           audience="medilink-mcp", issuer="medilink-actions")
        assert token["arguments_digest"] == hashlib.sha256(checked_json(arguments,65536,"MCP_INPUT_INVALID")).hexdigest()
        assert token["idempotency_key"] == meta["medilink"]["idempotency_key"]
        assert token["operation"] == ("ehr.create_patient" if name == "create_patient" else "insurance.submit_claim")
        assert token["token_type"] == "approved_action"
        peer.contexts.append(token)
        peer.calls.append((name,arguments))
        if peer.before_call:
            peer.before_call()
        if peer.write_mode == "timeout":
            await asyncio.sleep(1)
        result = PATIENT if name == "create_patient" else {**arguments,"covered":True,"claim_id":"CLAIM-SYNTHETIC","status":"submitted"}
        if peer.write_mode == "malformed":
            result = {"invalid":"receipt"}
        return SimpleNamespace(isError=False, structuredContent=result)

    peer.list_tools = listing
    peer.call_tool = call
    return env, peer


def state(env,case):
    return env.client.get(f"/api/workflows/{case['workflow']['id']}/case",headers=env.headers()).json()["data"]


def history(env,case,role="patient"):
    response = env.client.get(f"/api/workflows/{case['workflow']['id']}/approvals",headers=env.headers(role))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    return response.json()["data"]


def decide(env,case,*,approve=True,role="admin",approval=None):
    return env.client.post(f"/api/workflows/{case['workflow']['id']}/{'approve' if approve else 'reject'}",
        headers=env.headers(role),json={"expected_version":case["workflow"]["version"],
        "approval_id":(approval or history(env,case)[-1])["id"],"reason":"Reviewed authoritative evidence"})


def ready(env,peer,kind="onboarding"):
    if kind == "onboarding":
        peer.matches = []
        response = advance(env,start_onboarding(env).json()["data"])
        assert response.status_code == 200, response.text
        return response.json()["data"]
    return prepare_claim(env,start_claim(env,confirmed_profile(env)).json()["data"])


def sql(env,statement,params=()):
    with connection(env.settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(statement,params)
            return cursor.fetchall() if cursor.description else None


@pytest.mark.parametrize("kind,risk",[("onboarding","medium"),("claim","high")])
def test_review_execute_and_receipt_are_persisted_once(approvals,kind,risk):
    env,peer = approvals
    case = ready(env,peer,kind)
    pending = history(env,case)[0]
    assert pending["status"] == "pending" and pending["risk"] == risk
    assert advance(env,case).status_code == 403
    assert decide(env,case).status_code == 200
    case = state(env,case)
    assert case["phase"] == "collecting"
    response = advance(env,case)
    assert response.status_code == 200,response.text
    finished = response.json()["data"]
    assert finished["phase"] == "confirmed"
    assert advance(env,case).status_code == 409
    approval = history(env,case)[0]
    assert approval["status"] == "consumed" and approval["decided_by"] == env.users["admin"]["user_id"]
    assert approval["consumed_call_id"] == peer.contexts[0]["jti"]
    endpoint = "patients" if kind == "onboarding" else "claims"
    entity = env.client.get(f"/api/{endpoint}/{case['entity_id']}",headers=env.headers()).json()["data"]
    assert entity["status"] == ("active" if kind == "onboarding" else "submitted")
    assert sql(env,"SELECT status FROM workflow_action_keys") == [("succeeded",)]
    trace = env.client.get(f"/api/workflows/{case['workflow']['id']}/tool-calls",headers=env.headers()).text
    assert SECRET not in trace and "approval_token" not in trace
    assert decide(env,finished).status_code == 409


@pytest.mark.parametrize("role",["doctor","patient","patient2","receptionist","auditor"])
def test_only_independent_authorized_reviewer_can_approve(approvals,role):
    env,peer = approvals
    case = ready(env,peer)
    assert decide(env,case,role=role).status_code in {403,404}
    assert history(env,case)[0]["status"] == "pending"
    assert len(peer.calls) == 1


def test_claim_requires_administrator_even_if_medium_is_automatic(approvals):
    env,peer = approvals
    env.settings = replace(env.settings,medium_risk_review="automatic")
    env.client.app.state.settings = env.settings
    case = ready(env,peer,"claim")
    assert history(env,case)[0]["status"] == "pending"
    assert decide(env,case,role="doctor").status_code == 403


def test_automatic_medium_policy_is_recorded_and_single_use(approvals):
    env,peer = approvals
    env.settings = replace(env.settings,medium_risk_review="automatic")
    env.client.app.state.settings = env.settings
    case = ready(env,peer)
    approval = history(env,case)[0]
    assert case["phase"] == "collecting" and approval["decision_mode"] == "automatic"
    assert approval["decided_by"] == "system:risk-policy"
    assert advance(env,case).json()["data"]["phase"] == "confirmed"
    assert sql(env,"SELECT event FROM approval_events ORDER BY created_at") == [("requested",),("automatic",),("consumed",)]


@pytest.mark.parametrize("kind",["onboarding","claim"])
def test_rejection_is_terminal_and_never_calls_write(approvals,kind):
    env,peer = approvals
    case = ready(env,peer,kind)
    calls = len(peer.calls)
    assert decide(env,case,approve=False).status_code == 200
    case = state(env,case)
    assert case["phase"] == "rejected" and case["workflow"]["status"] == "REJECTED"
    assert case["next_action"] is None and advance(env,case).status_code == 409
    assert len(peer.calls) == calls


@pytest.mark.parametrize("change",["expiry","evidence","document","policy","reviewer"])
def test_changed_approval_scope_blocks_dispatch_and_can_be_requeued(approvals,change):
    env,peer = approvals
    case = ready(env,peer)
    assert decide(env,case).status_code == 200
    case = state(env,case)
    if change == "expiry":
        sql(env,"UPDATE approvals SET expires_at=clock_timestamp()-interval '1 second'")
    elif change == "evidence":
        sql(env,"UPDATE workflow_case_evidence SET facts='{" + '"matching_records":0,"changed":true' + "}'::jsonb")
    elif change == "document":
        # Binding even an otherwise valid document requires a fresh independent decision.
        doc = env.client.post("/api/documents",headers=env.headers(),files={"file":("evidence.txt",b"Synthetic","text/plain")}).json()["data"]
        sql(env,"UPDATE documents SET extraction_status='completed' WHERE id=%s",(doc["id"],))
        sql(env,"INSERT INTO workflow_documents (workflow_id,document_id) VALUES (%s,%s)",(case["workflow"]["id"],doc["id"]))
    elif change == "policy":
        env.settings = replace(env.settings,medium_risk_review="automatic")
        env.client.app.state.settings = env.settings
    else:
        sql(env,"UPDATE users SET is_active=FALSE WHERE role='admin'")
    response = advance(env,case)
    assert response.status_code == 409,response.text
    assert len(peer.calls) == 1 and history(env,case)[0]["status"] == "approved"
    response = env.client.post(f"/api/workflows/{case['workflow']['id']}/approvals",headers=env.headers(),
                              json={"expected_version":case["workflow"]["version"],"reason":"Refresh stale action"})
    assert response.status_code == 200,response.text
    assert history(env,case)[0]["status"] == "invalidated"


def test_canonical_preview_and_arguments_cannot_be_substituted(approvals):
    env,peer = approvals
    case = ready(env,peer)
    approval = history(env,case)[0]
    url = f"/api/workflows/{case['workflow']['id']}"
    response = env.client.get(f"{url}/approvals/{approval['id']}/review",headers=env.headers("admin"))
    assert response.status_code == 200,response.text
    args = response.json()["data"]["arguments"]
    assert response.json()["data"]["scope_matches"] is True
    assert decide(env,case).status_code == 200
    case = state(env,case)
    step = case["workflow"]["steps"][-1]
    body = {"expected_version":case["workflow"]["version"],"step_id":step["id"],"operation":"ehr.create_patient",
            "arguments":{**args,"contact_email":"other@example.com"}}
    assert env.client.post(f"{url}/tool-calls",headers=env.headers("doctor"),json=body).status_code == 409
    assert len(peer.calls) == 1
    body["risk"] = "low"
    assert env.client.post(f"{url}/tool-calls",headers=env.headers("doctor"),json=body).status_code == 422
    sql(env,"UPDATE workflow_steps SET risk='low' WHERE id=%s",(step["id"],))
    assert advance(env,case).status_code == 200
    assert sql(env,"SELECT risk FROM tool_calls WHERE approval_id IS NOT NULL") == [("medium",)]


def test_missing_signing_key_does_not_consume_approval(approvals):
    env,peer = approvals
    case = ready(env,peer)
    assert decide(env,case).status_code == 200
    case = state(env,case)
    env.client.app.state.settings = replace(env.settings,action_signing_secret=None)
    assert advance(env,case).status_code == 503
    assert history(env,case)[0]["status"] == "approved" and len(peer.calls) == 1
    assert sql(env,"SELECT count(*) FROM workflow_action_keys") == [(0,)]


@pytest.mark.parametrize("mode",["timeout","malformed"])
@pytest.mark.parametrize("kind",["onboarding","claim"])
def test_uncertain_write_is_never_retried_or_recreated(approvals,mode,kind):
    env,peer = approvals
    case = ready(env,peer,kind)
    assert decide(env,case).status_code == 200
    case = state(env,case)
    peer.write_mode = mode
    response = advance(env,case)
    assert response.status_code in {502,504},response.text
    assert sql(env,"SELECT status FROM workflow_action_keys") == [("uncertain",)]
    assert history(env,case)[0]["status"] == "consumed"
    assert advance(env,state(env,case)).status_code == 409
    if kind == "onboarding":
        assert start_onboarding(env).status_code == 409
    else:
        profile = sql(env,"SELECT patient_profile_id FROM claims WHERE id=%s",(case["entity_id"],))[0][0]
        assert start_claim(env,str(profile)).status_code == 409
    assert len(peer.contexts) == 1


def test_cancel_invalidates_review_and_inflight_write_cannot_be_cancelled(approvals):
    env,peer = approvals
    case = ready(env,peer)
    assert decide(env,case).status_code == 200
    case = state(env,case)
    def attempt_cancel():
        with pytest.raises(HTTPException) as exc:
            cancel_workflow(env.settings,case["workflow"]["id"],env.users["patient"],case["workflow"]["version"]+1)
        assert exc.value.status_code == 409
    peer.before_call = attempt_cancel
    assert advance(env,case).status_code == 200


def test_cancel_before_dispatch_invalidates_approval(approvals):
    env,peer = approvals
    case = ready(env,peer)
    cancel_workflow(env.settings,case["workflow"]["id"],env.users["patient"],case["workflow"]["version"])
    assert history(env,case)[0]["status"] == "invalidated"
    assert decide(env,state(env,case)).status_code == 409
    assert len(peer.calls) == 1


def test_concurrent_decisions_and_execution_have_one_winner(approvals):
    env,peer = approvals
    case = ready(env,peer)
    approval = history(env,case)[0]
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: decide(env,case,approval=approval),range(2)))
    assert sorted(r.status_code for r in responses) == [200,409]
    case = state(env,case)
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: advance(env,case),range(2)))
    assert sorted(r.status_code for r in responses) == [200,409]
    assert len(peer.contexts) == 1


def test_audit_events_are_append_only_and_metadata_access_is_scoped(approvals):
    env,peer = approvals
    case = ready(env,peer)
    with pytest.raises(psycopg2.Error):
        sql(env,"DELETE FROM approval_events")
    assert history(env,case,"auditor")[0]["status"] == "pending"
    assert env.client.get(f"/api/workflows/{case['workflow']['id']}/approvals",headers=env.headers("patient2")).status_code == 404
    assert env.client.get("/api/approvals",headers=env.headers("patient")).status_code == 403
    queue = env.client.get("/api/approvals",headers=env.headers("admin")).json()["data"]
    assert len(queue) == 1 and "scope_fingerprint" not in queue[0]


@pytest.mark.parametrize("operation,risk",[("ehr.get_patient","low"),("graph.find_dependencies","low"),
    ("ehr.create_patient","medium"),("scheduling.book_slot","medium"),
    ("ehr.update_patient_medications","high"),("insurance.submit_claim","high")])
def test_risk_is_owned_by_server(operation,risk):
    assert classify(operation) == risk


@pytest.mark.parametrize("function",[patient_service.update_patient_conditions,patient_service.update_patient_medications,
    patient_service.update_patient_allergies])
def test_legacy_clinical_writes_cannot_bypass_review(monkeypatch,function):
    def unexpected_sql():
        pytest.fail("Unauthorized clinical write reached database")
    monkeypatch.setattr(patient_service,"get_db_connection",unexpected_sql)
    with pytest.raises(PermissionError,match="approved managed workflow"):
        function("PATIENT-SYNTHETIC","value")


def test_graph_revision_is_bound_to_claim_approval(approvals):
    import httpx
    from backend.graph.client import GraphClient
    from test_graph import GraphPeer
    env,peer = approvals
    graph = GraphPeer()
    env.settings = replace(env.settings,graph_service_url="http://127.0.0.1:8103",graph_timeout_seconds=0.1)
    env.client.app.state.settings = env.settings
    env.client.app.state.graph_client = GraphClient(lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(graph.handle),**kwargs))
    case = prepare_claim(env,start_claim(env,confirmed_profile(env),{"use_graph":True}).json()["data"])
    assert history(env,case) == []
    for _ in range(3):
        response = advance(env,case)
        assert response.status_code == 200,response.text
        case = response.json()["data"]
    assert decide(env,case).status_code == 200
    case = state(env,case)
    sql(env,"UPDATE workflow_graph_queries SET graph_revision='revision:changed'")
    assert advance(env,case).status_code == 409
    assert peer.contexts == []


def test_expired_approval_cannot_be_approved_and_reason_is_required(approvals):
    env,peer = approvals
    case = ready(env,peer)
    sql(env,"UPDATE approvals SET expires_at=clock_timestamp()-interval '1 second'")
    assert decide(env,case).status_code == 409
    approval = history(env,case)[0]
    response = env.client.post(f"/api/workflows/{case['workflow']['id']}/approve",headers=env.headers("admin"),
        json={"expected_version":case["workflow"]["version"],"approval_id":approval["id"],"reason":"   "})
    assert response.status_code == 422
    assert sql(env,"SELECT event FROM approval_events") == [("requested",)]


def test_pre_dispatch_capability_failure_cannot_reuse_consumed_approval(approvals):
    env,peer = approvals
    case = ready(env,peer)
    assert decide(env,case).status_code == 200
    case = state(env,case)
    async def no_tools():
        return SimpleNamespace(nextCursor=None,tools=[])
    peer.list_tools = no_tools
    assert advance(env,case).status_code == 502
    assert sql(env,"SELECT status FROM workflow_action_keys") == [("failed",)]
    assert history(env,case)[0]["status"] == "consumed" and peer.contexts == []
    assert advance(env,state(env,case)).status_code == 409


def test_invalid_risk_settings_and_secret_repr():
    from backend.core.config import Settings
    for options in ({"medium_risk_review":"skip"},{"approval_ttl_minutes":0},{"approval_ttl_minutes":1441}):
        with pytest.raises(ValueError):
            Settings(**options)
    assert SECRET not in repr(Settings(action_signing_secret=SECRET))
