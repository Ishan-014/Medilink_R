from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.db.connection import connection
from backend.services.idempotency import reserve_action, record_action_outcome

BODY = {"workflow_type": "onboarding", "request": "Register synthetic patient"}


def create(api_env, *, role="patient", key=None, body=None):
    headers = api_env.headers(role)
    if key:
        headers["Idempotency-Key"] = key
    return api_env.client.post("/api/workflows", headers=headers, json=body or BODY)


def transition(api_env, run, status, role="doctor"):
    return api_env.client.post(f"/api/workflows/{run['id']}/transitions", headers=api_env.headers(role),
                               json={"expected_version": run["version"], "status": status})


def test_workflow_persistence_plan_order_and_state_flow(api_env):
    response = create(api_env)
    assert response.status_code == 201
    run = response.json()["data"]
    assert run["owner_user_id"] == api_env.users["patient"]["user_id"]
    assert run["status"] == "CREATED"
    run = transition(api_env, run, "PLANNING").json()["data"]
    response = api_env.client.put(f"/api/workflows/{run['id']}/plan", headers=api_env.headers("doctor"), json={
        "expected_version": run["version"], "steps": [{"action": "validate_patient"}, {"action": "create_record"}],
    })
    assert response.status_code == 200
    run = response.json()["data"]
    assert [s["position"] for s in run["steps"]] == [0, 1]
    assert [s["action"] for s in run["steps"]] == ["validate_patient", "create_record"]
    response = transition(api_env, run, "EXECUTING")
    assert response.status_code == 200
    run = response.json()["data"]
    assert run["version"] == 3
    assert api_env.client.get(f"/api/workflows/{run['id']}", headers=api_env.headers()).json()["data"] == run
    assert transition(api_env, run, "COMPLETED").status_code == 409
    assert transition(api_env, run, "WAITING_FOR_DATA").status_code == 200


def test_patient_ownership_and_auditor_read_only(api_env):
    run = create(api_env).json()["data"]
    url = f"/api/workflows/{run['id']}"
    assert api_env.client.get(url, headers=api_env.headers("patient2")).status_code == 404
    assert api_env.client.get("/api/workflows", headers=api_env.headers("patient2")).json()["data"] == []
    assert api_env.client.get(url, headers=api_env.headers("auditor")).status_code == 200
    assert transition(api_env, run, "PLANNING", role="auditor").status_code == 403
    assert transition(api_env, run, "PLANNING", role="patient").status_code == 403
    assert create(api_env, role="auditor").status_code == 403
    assert api_env.client.post(url + "/cancel", headers=api_env.headers("patient2"), json={"expected_version": 0}).status_code == 404
    assert api_env.client.post(url + "/cancel", headers=api_env.headers(), json={"expected_version": 0}).status_code == 200


def test_stale_and_invalid_transitions_do_not_change_state(api_env):
    run = create(api_env).json()["data"]
    assert transition(api_env, run, "COMPLETED").status_code == 409
    assert transition(api_env, run, "PLANNING").status_code == 200
    assert transition(api_env, run, "PLANNING").status_code == 409
    current = api_env.client.get(f"/api/workflows/{run['id']}", headers=api_env.headers()).json()["data"]
    assert current["status"] == "PLANNING"
    assert current["version"] == 1
    assert transition(api_env, current, "EXECUTING").status_code == 409  # No plan yet.


def test_waiting_for_approval_cannot_be_resumed_without_approval_service(api_env):
    run = create(api_env).json()["data"]
    with connection(api_env.settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE workflow_runs SET status = 'WAITING_FOR_APPROVAL' WHERE id = %s", (run["id"],))
    assert transition(api_env, run, "EXECUTING", role="admin").status_code == 409
    assert transition(api_env, run, "COMPLETED", role="admin").status_code == 409


def test_terminal_workflow_cannot_resume(api_env):
    run = create(api_env).json()["data"]
    cancelled = api_env.client.post(f"/api/workflows/{run['id']}/cancel", headers=api_env.headers(),
                                    json={"expected_version": 0}).json()["data"]
    assert transition(api_env, cancelled, "PLANNING").status_code == 409


def test_idempotent_creation_replays_and_conflicting_payload_is_rejected(api_env):
    first = create(api_env, key="request-1")
    again = create(api_env, key="request-1")
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["data"]["id"] == again.json()["data"]["id"]
    assert create(api_env, key="request-1", body={**BODY, "request": "Different request"}).status_code == 409
    assert create(api_env, role="patient2", key="request-1").status_code == 201


def test_concurrent_creation_returns_one_workflow(api_env):
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: create(api_env, key="concurrent-request"), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 201]
    assert len({r.json()["data"]["id"] for r in responses}) == 1


def test_concurrent_transition_allows_one_writer(api_env):
    run = create(api_env).json()["data"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: transition(api_env, run, "PLANNING"), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409]


def test_workflow_input_validation_pagination_and_unauthenticated_access(api_env):
    client = api_env.client
    assert client.post("/api/workflows", json=BODY).status_code == 401
    assert create(api_env, body={**BODY, "owner_user_id": "forged"}).status_code == 422
    assert create(api_env, body={**BODY, "document_ids": [str(uuid4())]}).status_code == 404
    assert client.get("/api/workflows?limit=101", headers=api_env.headers()).status_code == 422
    assert client.get("/api/workflows/not-a-uuid", headers=api_env.headers()).status_code == 422
    for _ in range(3):
        assert create(api_env).status_code == 201
    assert len(client.get("/api/workflows?limit=2&offset=1", headers=api_env.headers()).json()["data"]) == 2


def test_external_action_reservation_survives_retries_and_rejects_payload_changes(api_env):
    run = create(api_env).json()["data"]
    args = (api_env.settings, run["id"], "create_patient", "action-1", {"patient": "synthetic"})
    first = reserve_action(*args)
    assert first.should_execute
    assert not reserve_action(*args).should_execute
    record_action_outcome(api_env.settings, first.id, "succeeded", "ehr:confirmed-1")
    again = reserve_action(*args)
    assert not again.should_execute and again.result_reference == "ehr:confirmed-1"
    with pytest.raises(HTTPException) as error:
        reserve_action(*args[:-1], {"patient": "different"})
    assert error.value.status_code == 409


def test_uncertain_action_is_not_automatically_retried(api_env):
    run = create(api_env).json()["data"]
    args = (api_env.settings, run["id"], "submit_claim", "action-1", {"claim": "synthetic"})
    first = reserve_action(*args)
    record_action_outcome(api_env.settings, first.id, "uncertain")
    assert reserve_action(*args).status == "uncertain"
    assert not reserve_action(*args).should_execute
    with pytest.raises(HTTPException):
        record_action_outcome(api_env.settings, first.id, "succeeded", "fabricated")


def test_concurrent_action_reservation_has_one_executor(api_env):
    run = create(api_env).json()["data"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(pool.map(lambda _: reserve_action(
            api_env.settings, run["id"], "create_patient", "same-key", {"patient": "synthetic"}), range(2)))
    assert sum(r.should_execute for r in reservations) == 1
    assert len({r.id for r in reservations}) == 1
