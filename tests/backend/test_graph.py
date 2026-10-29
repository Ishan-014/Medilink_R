import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest

from backend.db.connection import connection
from backend.graph.client import GraphClient
from backend.services.workflow_service import cancel_workflow
from test_business_workflows import business as business
from test_business_workflows import confirmed_profile, start_claim, advance, prepare_claim
from test_integrations import workflow


class GraphPeer:
    def __init__(self):
        self.mode = "ok"
        self.requests = []
        self.resolved = True
        self.before_query = None
        self.authorization = []

    async def handle(self, request):
        self.authorization.append(request.headers.get("Authorization"))
        if self.mode == "timeout":
            await asyncio.sleep(1)
        if self.mode == "unavailable":
            return httpx.Response(503, json={"private": "internal graph detail"})
        if self.mode == "redirect":
            return httpx.Response(302, headers={"location": "https://another.example/secret"})
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ready"})
        body = json.loads(request.content)
        self.requests.append(body)
        if self.before_query:
            self.before_query(body)
        items = {
            "query_relationships": [{"kind": "relationship", "source_reference": "patient:SYNTHETIC", "relation": "HAS_CLAIM", "target_reference": "claim:PRIOR"}],
            "find_dependencies": [{"kind": "dependency", "reference": "procedure:REQUIRED", "resolved": self.resolved}],
            "claim_history": [{"kind": "claim", "reference": "claim:PRIOR", "outcome": "approved"}],
        }[body["operation"]]
        value = {"query_id": body["query_id"], "operation": body["operation"], "patient_id": body["patient_id"],
                 "result_reference": f"graph-result:{body['query_id']}", "graph_revision": "revision:1", "complete": True, "items": items}
        if self.mode == "wrong_patient":
            value["patient_id"] = "ANOTHER-PATIENT"
        elif self.mode == "wrong_query":
            value["query_id"] = str(uuid4())
        elif self.mode == "incomplete":
            value["complete"] = False
        elif self.mode == "raw_document":
            value["raw_document_text"] = "private clinical document"
        elif self.mode == "wrong_item":
            value["items"] = [{"kind": "dependency", "reference": "procedure:UNRELATED", "resolved": True}]
        elif self.mode == "duplicates":
            value["items"] = items + items
        elif self.mode == "empty":
            value["items"] = []
        elif self.mode == "oversize":
            return httpx.Response(200, content=b"x" * (1024 * 1024 + 1), headers={"content-type": "application/json"})
        elif self.mode == "not_json":
            return httpx.Response(200, text="private server detail")
        elif self.mode == "nonfinite":
            return httpx.Response(200, content=json.dumps(value).replace('"complete": true', '"complete": NaN').encode(), headers={"content-type": "application/json"})
        elif self.mode == "coerced_true":
            value["complete"] = 1
        return httpx.Response(200, json=value)


@pytest.fixture
def graph(business):
    env, hospital = business
    peer = GraphPeer()
    env.settings = replace(env.settings, graph_service_url="http://127.0.0.1:8103", graph_service_token="private-graph-test-token",
                           graph_timeout_seconds=0.1)
    env.client.app.state.settings = env.settings
    factory = lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(peer.handle), **kwargs)
    env.client.app.state.graph_client = GraphClient(factory)
    return env, hospital, peer


def graph_workflow(env, role="patient"):
    if role == "patient":
        return workflow(env, ("graph.claim_history",))
    created = env.client.post("/api/workflows", headers=env.headers(role), json={"workflow_type": "onboarding", "request": "Synthetic graph context"}).json()["data"]
    base = f"/api/workflows/{created['id']}"
    env.client.post(base + "/transitions", headers=env.headers("doctor"), json={"expected_version": 0, "status": "PLANNING"})
    env.client.put(base + "/plan", headers=env.headers("doctor"), json={"expected_version": 1, "steps": [{"action": "graph.claim_history"}]})
    return env.client.post(base + "/transitions", headers=env.headers("doctor"), json={"expected_version": 2, "status": "EXECUTING"}).json()["data"]


def query(env, run, profile, *, role="doctor", operation="claim_history"):
    return env.client.post(f"/api/workflows/{run['id']}/graph-queries", headers=env.headers(role), json={
        "expected_version": run["version"], "step_id": run["steps"][0]["id"], "patient_profile_id": profile, "operation": operation,
    })


def graph_claim(env):
    profile = confirmed_profile(env)
    case = prepare_claim(env, start_claim(env, profile, {"use_graph": True}).json()["data"])
    assert case["phase"] == "collecting" and case["next_action"] == "graph.query_relationships"
    return case


def test_graph_read_is_validated_linked_and_metadata_only_is_persisted(graph):
    env, _, peer = graph
    profile = confirmed_profile(env)
    run = graph_workflow(env)
    def pending(body):
        with connection(env.settings) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT status FROM workflow_graph_queries WHERE id=%s", (body["query_id"],))
                assert cursor.fetchone()[0] == "pending"
    peer.before_query = pending
    response = query(env, run, profile)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["query"]["status"] == "succeeded" and data["query"]["item_count"] == 1
    assert data["result"]["items"][0]["reference"] == "claim:PRIOR"
    trace = env.client.get(f"/api/workflows/{run['id']}/graph-queries", headers=env.headers("auditor"))
    assert trace.status_code == 200 and len(trace.json()["data"]) == 1
    assert "claim:PRIOR" not in trace.text and "private-graph" not in trace.text and "patient_id" not in trace.text
    with connection(env.settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT row_to_json(q)::text FROM workflow_graph_queries q")
            stored = cursor.fetchone()[0]
            assert "claim:PRIOR" not in stored and "items" not in stored
    current = env.client.get(f"/api/workflows/{run['id']}", headers=env.headers()).json()["data"]
    assert current["steps"][0]["result_reference"] == "graph-query:" + data["query"]["id"]
    assert response.headers["Cache-Control"] == "private, no-store"
    assert peer.authorization == ["Bearer private-graph-test-token"]


def test_graph_dependent_claim_requires_all_three_queries(graph):
    env, _, peer = graph
    case = graph_claim(env)
    for index in range(3):
        response = advance(env, case)
        assert response.status_code == 200
        case = response.json()["data"]
        assert case["phase"] == ("awaiting_approval" if index == 2 else "collecting")
    assert case["workflow"]["status"] == "WAITING_FOR_APPROVAL"
    assert [request["operation"] for request in peer.requests] == ["query_relationships", "find_dependencies", "claim_history"]
    assert advance(env, case).status_code == 403
    claim = env.client.get(f"/api/claims/{case['entity_id']}", headers=env.headers()).json()["data"]
    assert claim["status"] == "prepared" and claim["external_claim_id"] is None


def test_unresolved_dependencies_pause_and_explicit_refresh_ignores_old_results(graph):
    env, _, peer = graph
    peer.resolved = False
    case = graph_claim(env)
    case = advance(env, case).json()["data"]
    case = advance(env, case).json()["data"]
    assert case["phase"] == "waiting_data" and case["missing_fields"] == ["graph_dependencies"]
    # Graph requirements cannot be removed from a managed case through intake.
    base = f"/api/claims/{case['workflow']['id']}/intake"
    assert env.client.patch(base, headers=env.headers(), json={"expected_version": case["workflow"]["version"], "use_graph": False}).status_code == 422
    peer.resolved = True
    case = env.client.patch(base, headers=env.headers(), json={"expected_version": case["workflow"]["version"]}).json()["data"]
    case = prepare_claim(env, case)
    assert case["phase"] == "collecting"
    for _ in range(3):
        case = advance(env, case).json()["data"]
    assert case["phase"] == "awaiting_approval"
    trace = env.client.get(f"/api/workflows/{case['workflow']['id']}/graph-queries", headers=env.headers()).json()["data"]
    assert len(trace) == 5


@pytest.mark.parametrize("mode,code,status", [
    ("unavailable", "GRAPH_UNAVAILABLE", 503), ("redirect", "GRAPH_UNAVAILABLE", 503),
    ("timeout", "GRAPH_TIMEOUT", 504), ("wrong_patient", "GRAPH_RESULT_INVALID", 502),
    ("wrong_query", "GRAPH_RESULT_INVALID", 502), ("incomplete", "GRAPH_INCOMPLETE", 502),
    ("raw_document", "GRAPH_RESULT_INVALID", 502), ("duplicates", "GRAPH_RESULT_INVALID", 502),
    ("wrong_item", "GRAPH_RESULT_INVALID", 502), ("oversize", "GRAPH_RESULT_INVALID", 502),
    ("not_json", "GRAPH_RESULT_INVALID", 502),
    ("nonfinite", "GRAPH_RESULT_INVALID", 502), ("coerced_true", "GRAPH_RESULT_INVALID", 502),
])
def test_failures_are_persisted_and_never_report_workflow_success(graph, mode, code, status):
    env, _, peer = graph
    profile = confirmed_profile(env)
    run = graph_workflow(env)
    peer.mode = mode
    response = query(env, run, profile)
    assert response.status_code == status and response.json()["error"]["code"] == code
    assert "private" not in response.text
    trace = env.client.get(f"/api/workflows/{run['id']}/graph-queries", headers=env.headers()).json()["data"]
    assert trace[0]["status"] == "failed" and trace[0]["result_reference"] is None
    current = env.client.get(f"/api/workflows/{run['id']}", headers=env.headers()).json()["data"]
    assert current["status"] == "FAILED" and current["steps"][0]["status"] == "FAILED"


def test_graph_failure_fails_a_required_claim_without_preparing_it(graph):
    env, _, peer = graph
    case = graph_claim(env)
    peer.mode = "unavailable"
    assert advance(env, case).status_code == 503
    case = env.client.get(f"/api/workflows/{case['workflow']['id']}/case", headers=env.headers()).json()["data"]
    assert case["phase"] == "failed" and case["workflow"]["status"] == "FAILED"
    assert env.client.get(f"/api/claims/{case['entity_id']}", headers=env.headers()).json()["data"]["status"] == "failed"


def test_empty_complete_results_are_distinct_from_unavailability(graph):
    env, _, peer = graph
    peer.mode = "empty"
    assert query(env, graph_workflow(env), confirmed_profile(env)).json()["data"]["query"]["item_count"] == 0


@pytest.mark.parametrize("role", ["patient", "patient2", "receptionist", "auditor"])
def test_graph_execution_is_staff_clinical_only(graph, role):
    env, _, peer = graph
    profile = confirmed_profile(env)
    assert query(env, graph_workflow(env), profile, role=role).status_code == 403
    assert peer.requests == []


def test_subject_and_operation_binding_cannot_be_overridden(graph):
    env, _, peer = graph
    profile = confirmed_profile(env)
    run = graph_workflow(env, role="patient2")
    assert query(env, run, profile).status_code == 409
    run = graph_workflow(env)
    assert query(env, run, profile, operation="find_dependencies").status_code == 409
    body = {"expected_version": run["version"], "step_id": run["steps"][0]["id"], "patient_profile_id": profile,
            "operation": "claim_history", "query": "DROP SPACE hospital"}
    assert env.client.post(f"/api/workflows/{run['id']}/graph-queries", headers=env.headers("doctor"), json=body).status_code == 422
    assert env.client.get(f"/api/workflows/{run['id']}/graph-queries", headers=env.headers("patient2")).status_code == 404
    assert peer.requests == []


def test_duplicate_and_concurrent_queries_are_single_dispatch(graph):
    env, _, peer = graph
    profile = confirmed_profile(env)
    run = graph_workflow(env)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: query(env, run, profile), range(2)))
    assert sorted(r.status_code for r in replies) == [200, 409]
    assert len(peer.requests) == 1
    current = env.client.get(f"/api/workflows/{run['id']}", headers=env.headers()).json()["data"]
    assert query(env, current, profile).status_code == 409


def test_cancellation_during_graph_read_is_not_reopened(graph):
    env, _, peer = graph
    profile = confirmed_profile(env)
    run = graph_workflow(env)
    peer.before_query = lambda body: cancel_workflow(env.settings, run["id"], env.users["patient"], run["version"] + 1)
    assert query(env, run, profile).status_code == 200
    assert env.client.get(f"/api/workflows/{run['id']}", headers=env.headers()).json()["data"]["status"] == "CANCELLED"


def test_health_configuration_and_secrets_are_controlled(graph):
    env, _, _ = graph
    assert env.client.get("/api/graph/health", headers=env.headers("doctor")).status_code == 403
    response = env.client.get("/api/graph/health", headers=env.headers("admin"))
    assert response.status_code == 200 and response.json()["data"] == {"status": "ready"}
    assert "8103" not in response.text and "private-graph" not in response.text
    assert "private-graph" not in repr(env.settings)
    env.client.app.state.settings = replace(env.settings, graph_service_url=None)
    assert env.client.get("/api/graph/health", headers=env.headers("admin")).json()["error"]["code"] == "GRAPH_NOT_CONFIGURED"
    assert env.client.get("/api/health").status_code == 200


@pytest.mark.parametrize("url", ["http://remote.example", "https://name:password@example.com", "https://example.com/?token=secret", "file:///tmp/graph"])
def test_invalid_graph_endpoints_fail_without_network(graph, url):
    env, _, peer = graph
    env.client.app.state.settings = replace(env.settings, graph_service_url=url)
    response = env.client.get("/api/graph/health", headers=env.headers("admin"))
    assert response.status_code == 503 and response.json()["error"]["code"] == "GRAPH_CONFIG_INVALID"
    assert peer.authorization == []


def test_optional_graph_flag_preserves_existing_claim_idempotency(business):
    env, _ = business
    profile = confirmed_profile(env)
    body = {"patient_profile_id": profile, "policy_id": "POL-SYNTHETIC", "procedure_code": "PROC-SYNTHETIC", "amount": "125.50", "currency": "INR"}
    headers = {**env.headers(), "Idempotency-Key": "claim-existing-key"}
    first = env.client.post("/api/claims", headers=headers, json=body)
    replay = env.client.post("/api/claims", headers=headers, json={**body, "use_graph": False})
    assert first.status_code == 201 and replay.status_code == 200
    assert first.json()["data"]["workflow"]["id"] == replay.json()["data"]["workflow"]["id"]
    from backend.api.business_schemas import ClaimStart
    from backend.services.workflow_service import fingerprint
    old_body = ClaimStart(**body).model_dump(mode="json")
    old_body.pop("use_graph")
    with connection(env.settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT fingerprint FROM workflow_creation_keys WHERE idempotency_key=%s", ("claim-existing-key",))
            assert cursor.fetchone()[0] == fingerprint({"kind": "insurance_claim", "body": old_body})
    assert env.client.post("/api/claims", headers=headers, json={**body, "use_graph": True}).status_code == 409


def test_required_graph_is_not_silently_skipped_when_unconfigured(graph):
    env, _, _ = graph
    case = graph_claim(env)
    env.client.app.state.settings = replace(env.settings, graph_service_url=None)
    response = advance(env, case)
    assert response.status_code == 503 and response.json()["error"]["code"] == "GRAPH_NOT_CONFIGURED"
    current = env.client.get(f"/api/workflows/{case['workflow']['id']}/case", headers=env.headers()).json()["data"]
    assert current["phase"] == "failed" and current["workflow"]["status"] == "FAILED"
