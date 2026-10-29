"""SQL-backed audit, privacy, recovery and bounded-query regression checks."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import psycopg2
import pytest
from fastapi import HTTPException

from backend.core.config import Settings
from backend.core.errors import BackendError
from backend.db.connection import connection
from backend.observability.context import RequestContext, request_context
from backend.recovery.client import ReconciliationClient, validate_config
from backend.services import audit_service
from backend.services.tool_call_service import prepare
from backend.services.business_service import next_call
from test_approvals import approvals as approvals, ready, decide, state, history, sql
from test_business_workflows import business as business
from test_business_workflows import PATIENT, advance, start_onboarding


class ReceiptPeer:
    def __init__(self):
        self.requests = []
        self.headers = []
        self.mode = "ok"
        self.outcome = "succeeded"
        self.result = None

    async def handle(self, request):
        self.headers.append(request.headers.get("Authorization"))
        body = json.loads(request.content)
        self.requests.append(body)
        if self.mode == "timeout":
            await asyncio.sleep(1)
        if self.mode == "outage":
            return httpx.Response(503, json={"secret": "private failure"})
        if self.mode == "redirect":
            return httpx.Response(
                302, headers={"Location": "https://elsewhere.example/secret"}
            )
        if self.mode == "oversize":
            return httpx.Response(200, content=b"x" * 65537)
        if self.mode == "invalid_json":
            return httpx.Response(200, text="private upstream text")
        result = PATIENT if body["operation"] == "ehr.create_patient" else self.result
        response = {
            **body,
            "outcome": self.outcome,
            "external_reference": None
            if self.outcome == "pending"
            else "external-receipt:SYNTHETIC",
            "result": result if self.outcome == "succeeded" else None,
        }
        if self.mode == "wrong_binding":
            response["arguments_digest"] = "0" * 64
        if self.mode == "invalid_result":
            response["result"] = {"private": "bad receipt"}
        if self.mode == "wrong_patient":
            response["result"] = {**PATIENT, "email": "other@example.com"}
        if self.mode == "extra_field":
            response["secret"] = "upstream internal data"
        return httpx.Response(200, json=response)


@pytest.fixture
def recovery(approvals):
    env, hospital = approvals
    env.settings = replace(
        env.settings,
        recovery_service_url="http://127.0.0.1:8300",
        recovery_service_token="private-recovery-test-token",
        recovery_timeout_seconds=0.1,
    )
    env.client.app.state.settings = env.settings
    peer = ReceiptPeer()
    env.client.app.state.reconciliation_client = ReconciliationClient(
        lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(peer.handle), **kwargs
        )
    )
    return env, hospital, peer


def unresolved(env, hospital, kind="onboarding"):
    case = ready(env, hospital, kind)
    assert decide(env, case).status_code == 200
    case = state(env, case)
    hospital.write_mode = "timeout"
    assert advance(env, case).status_code == 504
    case = state(env, case)
    calls = env.client.get(
        f"/api/workflows/{case['workflow']['id']}/tool-calls",
        headers=env.headers("admin"),
    ).json()["data"]
    return case, calls[-1]


def reconcile(env, case, call, role="admin", **extra):
    return env.client.post(
        f"/api/workflows/{case['workflow']['id']}/tool-calls/{call['id']}/reconcile",
        headers=env.headers(role),
        json={
            "expected_version": case["workflow"]["version"],
            "reason": "Verify external durable receipt",
            **extra,
        },
    )


def test_audit_is_atomic_append_only_and_request_correlated(approvals):
    env, peer = approvals
    response = start_onboarding(env)
    case = response.json()["data"]
    rows = sql(
        env,
        "SELECT actor_user_id,request_id,metadata::text FROM audit_logs WHERE workflow_id=%s",
        (case["workflow"]["id"],),
    )
    assert rows and all(row[0] == env.users["patient"]["user_id"] for row in rows)
    assert all(row[1] == response.headers["X-Request-ID"] for row in rows)
    assert "Synthetic Patient" not in str(rows) and "contact_email" not in str(rows)
    before = sql(env, "SELECT count(*) FROM audit_logs")[0][0]
    with pytest.raises(RuntimeError):
        with connection(env.settings) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE workflow_runs SET version=version+1 WHERE id=%s",
                    (case["workflow"]["id"],),
                )
                raise RuntimeError("rollback")
    assert sql(env, "SELECT count(*) FROM audit_logs")[0][0] == before
    for table in ("audit_logs", "system_events"):
        if table == "system_events":
            env.client.get("/api/missing")
        for verb in (
            f"DELETE FROM {table}",
            f"UPDATE {table} SET actor_user_id='changed'",
        ):
            with pytest.raises(psycopg2.Error):
                sql(env, verb)


def test_trace_includes_history_failures_approval_and_recovery(recovery):
    env, hospital, peer = recovery
    case, call = unresolved(env, hospital)
    response = reconcile(env, case, call)
    assert response.status_code == 200, response.text
    response = env.client.get(
        f"/api/workflows/{case['workflow']['id']}/trace?limit=100",
        headers=env.headers("auditor"),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["workflow"]["status"] == "COMPLETED"
    resources = {e["resource_type"] for e in data["events"]}
    assert {
        "workflow_runs",
        "workflow_steps",
        "tool_calls",
        "approval_events",
        "external_reconciliations",
        "system_events",
    } <= resources
    assert any(e["action"] == "MCP_TIMEOUT" for e in data["events"])
    assert any(
        e["metadata"].get("previous_status") == "uncertain" for e in data["events"]
    )
    assert (
        "Synthetic Patient" not in response.text
        and "private-recovery" not in response.text
        and "approval_token" not in response.text
    )
    assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize("role", ["patient", "doctor", "receptionist"])
def test_global_audit_and_events_require_privileged_role(approvals, role):
    env, _ = approvals
    assert env.client.get("/api/audit", headers=env.headers(role)).status_code == 403
    assert (
        env.client.get("/api/system-events", headers=env.headers(role)).status_code
        == 403
    )


def test_trace_scope_pagination_filters_and_actor_isolation(approvals):
    env, peer = approvals
    case = ready(env, peer)
    url = f"/api/workflows/{case['workflow']['id']}/trace"
    assert env.client.get(url, headers=env.headers("patient2")).status_code == 404
    ids = []
    offset = 0
    while True:
        data = env.client.get(
            url, params={"limit": 3, "offset": offset}, headers=env.headers("auditor")
        ).json()["data"]
        assert len(data["events"]) <= 3
        for entry in data["events"]:
            if (
                entry["source"] == "system"
                and (entry["metadata"].get("http_status") or 0) >= 400
            ):
                assert entry["outcome"] in {"failure", "denied"}
        ids.extend(e["id"] for e in data["events"])
        if data["next_offset"] is None:
            break
        offset = data["next_offset"]
    assert len(ids) == len(set(ids)) and len(ids) > 3
    query = f"/api/audit?workflow_id={case['workflow']['id']}&actor_user_id={env.users['patient']['user_id']}&limit=2"
    rows = env.client.get(query, headers=env.headers("auditor")).json()["data"]["items"]
    assert len(rows) == 2 and all(
        row["actor_user_id"] == env.users["patient"]["user_id"] for row in rows
    )
    for query in (
        "limit=101",
        "offset=-1",
        "after=2026-01-01T00:00:00",
        "after=2026-02-01T00:00:00Z&before=2026-01-01T00:00:00Z",
    ):
        assert (
            env.client.get(
                "/api/audit?" + query, headers=env.headers("auditor")
            ).status_code
            == 422
        )


def test_successful_receipt_reconciles_without_replaying_write(recovery):
    env, hospital, peer = recovery
    case, call = unresolved(env, hospital)
    response = reconcile(env, case, call)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "succeeded"
    assert state(env, case)["phase"] == "confirmed"
    assert sql(env, "SELECT status FROM workflow_action_keys") == [("succeeded",)]
    assert history(env, case)[0]["status"] == "consumed"
    assert len(hospital.contexts) == 1 and len(peer.requests) == 1
    assert peer.headers == ["Bearer private-recovery-test-token"]
    assert "contact_email" not in json.dumps(
        peer.requests
    )  # Lookup is keyed by digest/reference, not raw arguments.
    assert reconcile(env, state(env, case), call).status_code == 409
    assert advance(env, state(env, case)).status_code == 409
    assert len(hospital.contexts) == 1


def test_claim_receipt_reconciles_authoritative_submission(recovery):
    env, hospital, peer = recovery
    case, call = unresolved(env, hospital, "claim")
    args = hospital.calls[-1][1]
    peer.result = {
        **args,
        "covered": True,
        "claim_id": "CLAIM-RECOVERED",
        "status": "submitted",
    }
    response = reconcile(env, case, call)
    assert response.status_code == 200, response.text
    assert state(env, case)["phase"] == "confirmed"
    assert sql(env, "SELECT status,external_claim_id FROM claims") == [
        ("submitted", "CLAIM-RECOVERED")
    ]
    assert len(hospital.contexts) == 1


def test_confirmed_not_applied_closes_attempt_and_requires_new_workflow(recovery):
    env, hospital, peer = recovery
    case, call = unresolved(env, hospital)
    peer.outcome = "not_applied"
    response = reconcile(env, case, call)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "not_applied"
    assert sql(env, "SELECT status FROM workflow_action_keys") == [("failed",)]
    assert state(env, case)["phase"] == "failed"
    assert history(env, case)[0]["status"] == "consumed"
    assert start_onboarding(env).status_code == 201
    assert len(hospital.contexts) == 1


def test_pending_receipt_keeps_write_unresolved_and_allows_only_more_lookups(recovery):
    env, hospital, peer = recovery
    case, call = unresolved(env, hospital)
    peer.outcome = "pending"
    assert reconcile(env, case, call).json()["data"]["status"] == "unresolved"
    assert sql(env, "SELECT status FROM workflow_action_keys") == [("uncertain",)]
    assert start_onboarding(env).status_code == 409
    peer.outcome = "succeeded"
    assert reconcile(env, state(env, case), call).status_code == 200
    assert len(hospital.contexts) == 1 and len(peer.requests) == 2


@pytest.mark.parametrize(
    "mode,code",
    [
        ("timeout", "RECOVERY_TIMEOUT"),
        ("outage", "RECOVERY_UNAVAILABLE"),
        ("redirect", "RECOVERY_UNAVAILABLE"),
        ("oversize", "RECOVERY_OUTPUT_INVALID"),
        ("invalid_json", "RECOVERY_OUTPUT_INVALID"),
        ("wrong_binding", "RECOVERY_OUTPUT_INVALID"),
        ("invalid_result", "RECOVERY_OUTPUT_INVALID"),
        ("wrong_patient", "RECOVERY_OUTPUT_INVALID"),
        ("extra_field", "RECOVERY_OUTPUT_INVALID"),
    ],
)
def test_failed_lookup_cannot_change_domain_or_replay_write(recovery, mode, code):
    env, hospital, peer = recovery
    case, call = unresolved(env, hospital)
    peer.mode = mode
    response = reconcile(env, case, call)
    assert response.status_code in {502, 503, 504}, response.text
    assert response.json()["error"]["code"] == code
    assert "private" not in response.text
    assert state(env, case)["phase"] == "failed"
    assert sql(env, "SELECT status FROM workflow_action_keys") == [("uncertain",)]
    assert sql(env, "SELECT status,error_code FROM external_reconciliations") == [
        ("failed", code)
    ]
    assert len(hospital.contexts) == 1


@pytest.mark.parametrize(
    "role", ["patient", "patient2", "doctor", "receptionist", "auditor"]
)
def test_reconciliation_is_admin_only(recovery, role):
    env, hospital, peer = recovery
    case, call = unresolved(env, hospital)
    assert reconcile(env, case, call, role=role).status_code == 403
    assert peer.requests == []
    assert sql(env, "SELECT count(*) FROM external_reconciliations") == [(0,)]


def test_fresh_pending_write_cannot_be_reconciled_but_stale_write_can(recovery):
    env, hospital, peer = recovery
    case = ready(env, hospital)
    assert decide(env, case).status_code == 200
    case = state(env, case)
    body = next_call(
        env.settings,
        case["workflow"]["id"],
        env.users["doctor"],
        case["workflow"]["version"],
    )
    call_id, *_ = prepare(
        env.settings, case["workflow"]["id"], env.users["doctor"], body
    )
    case = state(env, case)
    call = {"id": call_id}
    assert reconcile(env, case, call).json()["error"]["code"] == "RECOVERY_NOT_READY"
    sql(
        env,
        "UPDATE tool_calls SET started_at=clock_timestamp()-interval '10 minutes' WHERE id=%s",
        (call_id,),
    )
    response = reconcile(env, case, call)
    assert response.status_code == 200, response.text
    assert state(env, case)["phase"] == "confirmed"
    assert hospital.contexts == []


def test_reconcile_missing_configuration_and_caller_receipt_injection(recovery):
    env, hospital, peer = recovery
    case, call = unresolved(env, hospital)
    assert reconcile(env, case, call, result=PATIENT).status_code == 422
    assert reconcile(env, case, call, outcome="succeeded").status_code == 422
    env.client.app.state.settings = replace(env.settings, recovery_service_url=None)
    assert (
        reconcile(env, case, call).json()["error"]["code"] == "RECOVERY_NOT_CONFIGURED"
    )
    assert peer.requests == []


def test_concurrent_reconciliation_has_one_lookup_and_no_write_replay(recovery):
    env, hospital, peer = recovery
    case, call = unresolved(env, hospital)
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: reconcile(env, case, call), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len(peer.requests) == 1 and len(hospital.contexts) == 1


def test_trace_query_count_is_constant_as_history_grows(approvals, monkeypatch):
    from contextlib import contextmanager

    env, peer = approvals
    case = ready(env, peer)
    original = audit_service.connection
    queries = []

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def execute(self, statement, args=None):
            queries.append(statement)
            return self.cursor.execute(statement, args)

        def __getattr__(self, name):
            return getattr(self.cursor, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.cursor.close()

    class Conn:
        def __init__(self, conn):
            self.conn = conn

        def cursor(self, **kwargs):
            return Cursor(self.conn.cursor(**kwargs))

    @contextmanager
    def tracked(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            yield Conn(conn)

    monkeypatch.setattr(audit_service, "connection", tracked)
    for _ in range(20):
        sql(
            env,
            "UPDATE workflow_runs SET version=version+1 WHERE id=%s",
            (case["workflow"]["id"],),
        )
    result = audit_service.trace(
        env.settings, case["workflow"]["id"], env.users["auditor"], 5, 0
    )
    assert len(result["events"]) == 5 and result["next_offset"] == 5
    assert len(queries) == 3


@pytest.mark.parametrize(
    "exception,code,status",
    [
        (psycopg2.OperationalError, "DATABASE_UNAVAILABLE", 503),
        (psycopg2.errors.QueryCanceled, "DATABASE_TIMEOUT", 504),
        (psycopg2.IntegrityError, "DATABASE_CONFLICT", 409),
        (RuntimeError, "INTERNAL_ERROR", 500),
    ],
)
def test_exception_codes_and_structured_logs_do_not_expose_secrets(
    approvals, caplog, exception, code, status
):
    env, _ = approvals

    @env.client.app.get("/api/failure/{value}")
    def failure(value: str):
        raise exception("private-database-password")

    with caplog.at_level("INFO", logger="medilink.events"):
        response = env.client.get(
            "/api/failure/private-patient?token=private-query", headers=env.headers()
        )
    assert response.status_code == status and response.json()["error"]["code"] == code
    logs = [
        json.loads(r.message) for r in caplog.records if r.name == "medilink.events"
    ]
    assert any(
        e.get("route") == "/api/failure/{value}" and e["code"] == code for e in logs
    )
    assert "private-" not in json.dumps(logs) + response.text
    assert sql(
        env,
        "SELECT code FROM system_events WHERE request_id=%s",
        (response.headers["X-Request-ID"],),
    ) == [(code,)]


def test_ai_adapter_handoff_error_and_telemetry_outage_are_controlled(
    approvals, monkeypatch
):
    from backend.observability import telemetry

    env, _ = approvals

    @env.client.app.get("/api/ai-boundary")
    def unavailable():
        raise BackendError("AI_UNAVAILABLE")

    assert (
        env.client.get("/api/ai-boundary").json()["error"]["code"] == "AI_UNAVAILABLE"
    )

    def offline(*args, **kwargs):
        raise psycopg2.OperationalError("secret")

    monkeypatch.setattr(telemetry, "connection", offline)
    response = env.client.get("/api/ai-boundary")
    assert response.status_code == 503 and "secret" not in response.text


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.example",
        "https://user:secret@remote.example",
        "https://remote.example?token=secret",
        "file:///tmp/receipts",
    ],
)
def test_recovery_connection_configuration_fails_closed(url):
    with pytest.raises(BackendError):
        validate_config(Settings(recovery_service_url=url))


def test_context_does_not_escape_request_lifetime(approvals):
    env, peer = approvals
    assert request_context.get() is None
    ready(env, peer)
    assert request_context.get() is None
    token = request_context.set(RequestContext("test-context", "system:test"))
    try:
        with connection(env.settings) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT current_setting('medilink.actor')")
                assert cursor.fetchone()[0] == "system:test"
    finally:
        request_context.reset(token)


def test_abandoned_lookup_can_be_superseded_without_replaying_write(recovery):
    from backend.services import reconciliation_service as service
    from backend.recovery.contracts import ReconcileInput, LookupResult

    env, hospital, peer = recovery
    case, call = unresolved(env, hospital)
    request = service.prepare(
        env.settings,
        case["workflow"]["id"],
        call["id"],
        env.users["admin"],
        ReconcileInput(
            expected_version=case["workflow"]["version"], reason="First worker lookup"
        ),
    )
    assert reconcile(env, state(env, case), call).status_code == 409
    sql(
        env,
        "UPDATE external_reconciliations SET started_at=clock_timestamp()-interval '10 minutes'",
    )
    assert reconcile(env, state(env, case), call).status_code == 200
    late = LookupResult(
        **request.model_dump(),
        outcome="succeeded",
        external_reference="external-receipt:LATE",
        result=PATIENT,
    )
    with pytest.raises(HTTPException):
        service.finalize(env.settings, request, result=late)
    assert len(hospital.contexts) == 1
    assert sql(
        env, "SELECT status FROM external_reconciliations ORDER BY started_at"
    ) == [("failed",), ("succeeded",)]


def test_validation_masks_unknown_keys_and_structured_log_payloads(approvals, caplog):
    env, _ = approvals
    with caplog.at_level("INFO", logger="medilink.events"):
        response = start_onboarding(
            env, {"private-secret-key": "private-patient-input"}
        )
    assert response.status_code == 422
    assert "private-" not in response.text
    logs = [r.message for r in caplog.records if r.name == "medilink.events"]
    assert "private-" not in "".join(logs)


def test_write_receipt_with_conflicting_identity_stays_uncertain(recovery):
    env, hospital, peer = recovery
    case = ready(env, hospital)
    assert decide(env, case).status_code == 200
    case = state(env, case)
    original = hospital.call_tool

    async def wrong_identity(name, arguments, **kwargs):
        response = await original(name, arguments, **kwargs)
        response.structuredContent = {**PATIENT, "email": "another@example.com"}
        return response

    hospital.call_tool = wrong_identity
    assert advance(env, case).status_code == 502
    assert state(env, case)["phase"] == "failed"
    assert sql(env, "SELECT status FROM workflow_action_keys") == [("uncertain",)]
