import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.db.connection import connection
from backend.integrations.client import HospitalClient
from backend.integrations.config import ServerConfig, load_config
from backend.services.tool_call_service import finalize

INPUT = {"type": "object", "properties": {"patient_id": {"type": "string", "minLength": 1}},
         "required": ["patient_id"], "additionalProperties": False}
OUTPUT = {"type": "object", "properties": {"patient_id": {"type": "string"}},
          "required": ["patient_id"], "additionalProperties": False}


class FakeSession:
    def __init__(self):
        self.tools = [SimpleNamespace(name="get_patient", inputSchema=INPUT, outputSchema=OUTPUT),
                      SimpleNamespace(name="get_results", inputSchema=INPUT, outputSchema=OUTPUT)]
        self.calls = 0
        self.mode = "ok"
        self.before_call = None

    async def initialize(self):
        if self.mode == "offline":
            raise OSError("private endpoint and credentials")
        if self.mode == "slow_initialize":
            await asyncio.sleep(1)

    async def send_ping(self):
        pass

    async def list_tools(self):
        return SimpleNamespace(tools=self.tools, nextCursor=None)

    async def call_tool(self, name, arguments):
        self.calls += 1
        if self.before_call:
            self.before_call()
        if self.mode == "slow_call":
            await asyncio.sleep(1)
        if self.mode == "disconnected":
            raise OSError("private connection error")
        if self.mode == "malformed":
            return SimpleNamespace(isError=False, structuredContent={"unexpected": "private clinical data"})
        return SimpleNamespace(isError=self.mode == "remote_error", structuredContent={"patient_id": arguments["patient_id"]})


class FakeTransport:
    def __init__(self, session):
        self.client = session

    @asynccontextmanager
    async def session(self, server):
        yield self.client


@pytest.fixture
def gateway(api_env, tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": {
        name: {"transport": "streamable_http", "url": "http://127.0.0.1:8102/mcp", "timeout_seconds": 0.1}
        for name in ("ehr", "lab")
    }}))
    api_env.settings = replace(api_env.settings, mcp_config_file=path)
    api_env.client.app.state.settings = api_env.settings
    session = FakeSession()
    api_env.client.app.state.hospital_client = HospitalClient(FakeTransport(session))
    return api_env, session


def workflow(env, actions=("ehr.get_patient",)):
    created = env.client.post("/api/workflows", headers=env.headers(), json={"workflow_type": "onboarding", "request": "Synthetic request"}).json()["data"]
    base = f"/api/workflows/{created['id']}"
    assert env.client.post(base + "/transitions", headers=env.headers("doctor"), json={"expected_version": 0, "status": "PLANNING"}).status_code == 200
    assert env.client.put(base + "/plan", headers=env.headers("doctor"), json={"expected_version": 1, "steps": [{"action": a} for a in actions]}).status_code == 200
    return env.client.post(base + "/transitions", headers=env.headers("doctor"), json={"expected_version": 2, "status": "EXECUTING"}).json()["data"]


def call(env, run, *, role="doctor", index=0, arguments=None, operation=None):
    return env.client.post(f"/api/workflows/{run['id']}/tool-calls", headers=env.headers(role), json={
        "expected_version": run["version"], "step_id": run["steps"][index]["id"],
        "operation": operation or run["steps"][index]["action"],
        "arguments": arguments if arguments is not None else {"patient_id": "PAT-SYNTHETIC"},
    })


def test_two_capabilities_are_validated_and_traced(gateway):
    env, session = gateway
    run = workflow(env, ("ehr.get_patient", "lab.get_results"))
    first = call(env, run)
    assert first.status_code == 200
    first_call = first.json()["data"]["call"]
    assert first.json()["data"]["result"] == {"patient_id": "PAT-SYNTHETIC"}
    assert first_call["status"] == "succeeded" and first_call["finished_at"]
    run = env.client.get(f"/api/workflows/{run['id']}", headers=env.headers()).json()["data"]
    assert run["steps"][0]["status"] == "COMPLETED"
    assert run["steps"][0]["result_reference"] == first_call["result_reference"]
    assert call(env, run, index=1).status_code == 200
    assert session.calls == 2
    trace = env.client.get(f"/api/workflows/{run['id']}/tool-calls", headers=env.headers("auditor"))
    assert trace.status_code == 200
    assert [row["server"] for row in trace.json()["data"]] == ["ehr", "lab"]
    assert "PAT-SYNTHETIC" not in trace.text
    assert "request_fingerprint" not in trace.text
    with connection(env.settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT count(*), count(DISTINCT workflow_id) FROM tool_calls")
            assert cursor.fetchone() == (2, 1)


def test_pending_record_commits_before_network_dispatch(gateway):
    env, session = gateway
    run = workflow(env)
    def check():
        with connection(env.settings) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT status, finished_at FROM tool_calls")
                assert cursor.fetchone() == ("pending", None)
    session.before_call = check
    assert call(env, run).status_code == 200


def test_successful_tool_call_allows_confirmed_workflow_completion(gateway):
    env, _ = gateway
    run = workflow(env)
    assert call(env, run).status_code == 200
    current = env.client.get(f"/api/workflows/{run['id']}", headers=env.headers()).json()["data"]
    response = env.client.post(f"/api/workflows/{run['id']}/transitions", headers=env.headers("doctor"),
                              json={"expected_version": current["version"], "status": "COMPLETED"})
    assert response.status_code == 200 and response.json()["data"]["status"] == "COMPLETED"


def test_unexpected_adapter_error_is_recorded_as_uncertain(gateway):
    env, _ = gateway
    class BrokenClient:
        async def call(self, *args):
            raise ValueError("private adapter failure")
    env.client.app.state.hospital_client = BrokenClient()
    run = workflow(env)
    response = call(env, run)
    assert response.status_code == 503 and "private adapter" not in response.text
    trace = env.client.get(f"/api/workflows/{run['id']}/tool-calls", headers=env.headers()).json()["data"]
    assert trace[0]["status"] == "uncertain"


@pytest.mark.parametrize("mode,code,http_status,status", [
    ("offline", "MCP_UNAVAILABLE", 503, "failed"),
    ("slow_initialize", "MCP_TIMEOUT", 504, "failed"),
    ("slow_call", "MCP_TIMEOUT", 504, "uncertain"),
    ("disconnected", "MCP_UNAVAILABLE", 503, "uncertain"),
    ("malformed", "MCP_OUTPUT_INVALID", 502, "failed"),
    ("remote_error", "MCP_REMOTE_ERROR", 502, "failed"),
])
def test_failures_never_mark_success(gateway, mode, code, http_status, status):
    env, session = gateway
    session.mode = mode
    run = workflow(env)
    response = call(env, run)
    assert response.status_code == http_status
    assert response.json()["error"]["code"] == code
    assert "private" not in response.text
    trace = env.client.get(f"/api/workflows/{run['id']}/tool-calls", headers=env.headers()).json()["data"]
    assert len(trace) == 1 and trace[0]["status"] == status
    assert trace[0]["result_reference"] is None
    current = env.client.get(f"/api/workflows/{run['id']}", headers=env.headers()).json()["data"]
    assert current["status"] == "FAILED" and current["steps"][0]["status"] == "FAILED"
    assert call(env, current).status_code == 409
    assert session.calls <= 1


def test_input_validation_happens_before_remote_call(gateway):
    env, session = gateway
    response = call(env, workflow(env), arguments={"patient_id": 123, "unexpected": "secret"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "MCP_INPUT_INVALID"
    assert session.calls == 0 and "secret" not in response.text


@pytest.mark.parametrize("role", ["patient", "patient2", "receptionist", "auditor"])
def test_only_doctor_admin_can_invoke_tools(gateway, role):
    env, session = gateway
    assert call(env, workflow(env), role=role).status_code == 403
    assert session.calls == 0


@pytest.mark.parametrize("operation", ["ehr.update_record", "insurance.submit_claim", "scheduling.book_slot"])
def test_write_tools_are_blocked_even_for_admin(gateway, operation):
    env, session = gateway
    response = call(env, workflow(env, (operation,)), role="admin")
    assert response.status_code == 403 and response.json()["error"]["code"] == "MCP_WRITE_BLOCKED"
    assert session.calls == 0


def test_unlisted_tools_step_mismatch_and_order_are_blocked(gateway):
    env, session = gateway
    run = workflow(env, ("ehr.get_patient", "lab.get_results"))
    assert call(env, run, operation="ehr.arbitrary_sql").status_code == 403
    assert call(env, run, operation="ehr.get_record").status_code == 409
    assert call(env, run, index=1).status_code == 409
    assert session.calls == 0


def test_replay_and_concurrent_duplicate_never_invoke_twice(gateway):
    env, session = gateway
    run = workflow(env)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: call(env, run), range(2)))
    assert sorted(r.status_code for r in replies) == [200, 409]
    current = env.client.get(f"/api/workflows/{run['id']}", headers=env.headers()).json()["data"]
    assert call(env, current).status_code == 409
    assert session.calls == 1


def test_cancelled_workflow_is_not_reopened_when_result_arrives(gateway):
    env, session = gateway
    run = workflow(env)
    def cancel():
        with connection(env.settings) as conn:
            with conn.cursor() as cursor:
                cursor.execute("UPDATE workflow_runs SET status = 'CANCELLED' WHERE id = %s", (run["id"],))
    session.before_call = cancel
    response = call(env, run)
    assert response.status_code == 200
    assert env.client.get(f"/api/workflows/{run['id']}", headers=env.headers()).json()["data"]["status"] == "CANCELLED"
    with pytest.raises(HTTPException):
        finalize(env.settings, response.json()["data"]["call"]["id"], 1)


def test_health_and_trace_roles_and_redaction(gateway):
    env, _ = gateway
    assert env.client.get("/api/integrations", headers=env.headers("doctor")).status_code == 403
    listing = env.client.get("/api/integrations", headers=env.headers("admin"))
    assert listing.status_code == 200 and len(listing.json()["data"]) == 5
    assert "8102" not in listing.text and "url" not in listing.text
    assert env.client.get("/api/integrations/ehr/health", headers=env.headers("auditor")).status_code == 200
    assert env.client.get("/api/integrations/billing/health", headers=env.headers("admin")).json()["error"]["code"] == "MCP_NOT_CONFIGURED"
    assert env.client.get("/api/integrations/unknown/health", headers=env.headers("admin")).status_code == 422
    run = workflow(env)
    assert env.client.get(f"/api/workflows/{run['id']}/tool-calls", headers=env.headers("patient2")).status_code == 404


@pytest.mark.parametrize("config", [
    {"transport": "streamable_http", "url": "http://remote.example/mcp"},
    {"transport": "streamable_http", "url": "https://name:secret@example.com/mcp"},
    {"transport": "streamable_http", "url": "https://example.com/mcp?token=secret"},
    {"transport": "stdio", "command": "python", "url": "http://127.0.0.1/mcp"},
    {"transport": "stdio"},
    {"transport": "stdio", "command": "python", "timeout_seconds": 0},
])
def test_invalid_connection_configuration_is_rejected(config):
    with pytest.raises(ValidationError):
        ServerConfig(**config)


def test_missing_or_malformed_config_is_safely_reported(api_env, tmp_path):
    assert load_config(api_env.settings).servers == {}
    config = tmp_path / "invalid.json"
    config.write_text('{"private credential":')
    api_env.client.app.state.settings = replace(api_env.settings, mcp_config_file=config)
    response = api_env.client.get("/api/integrations", headers=api_env.headers("admin"))
    assert response.status_code == 503 and response.json()["error"]["code"] == "MCP_CONFIG_INVALID"
    assert "private credential" not in response.text


@pytest.mark.parametrize("kind", ["missing", "external_ref", "wrong_output", "no_output"])
def test_missing_and_invalid_contracts_are_rejected(gateway, kind):
    env, session = gateway
    if kind == "missing":
        session.tools = []
    elif kind == "external_ref":
        session.tools[0] = SimpleNamespace(name="get_patient", inputSchema={"$ref": "https://external.example/schema"}, outputSchema=OUTPUT)
    elif kind == "wrong_output":
        session.tools[0] = SimpleNamespace(name="get_patient", inputSchema=INPUT, outputSchema={"type": "not-a-type"})
    else:
        session.tools[0] = SimpleNamespace(name="get_patient", inputSchema=INPUT, outputSchema=None)
    response = call(env, workflow(env))
    assert response.status_code == 502 and session.calls == 0
