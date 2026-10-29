"""Part 9 cross-cutting backend security and read-scaling regressions."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from uuid import uuid4

import jwt
from psycopg2.extras import execute_values
import pytest

from backend.auth.security import decode_access_token, issue_access_token
from backend.db.connection import connection
from backend.ops import benchmark, healthcheck
from backend.services import workflow_service

PROTECTED_READS = (
    "/api/me",
    "/api/workflows",
    "/api/documents",
    "/api/integrations",
    "/api/approvals",
    "/api/audit",
    "/api/system-events",
    "/api/risk-policy",
    "/api/graph/health",
    "/api/workflows/{id}/trace",
    "/api/workflows/{id}/reconciliations",
    "/api/workflows/{id}/approvals",
)


@pytest.mark.parametrize("path", PROTECTED_READS)
def test_backend_read_boundaries_require_authentication(api_env, path):
    response = api_env.client.get(path.format(id=uuid4()))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "patch",
    [
        {"iss": "untrusted-issuer"},
        {"aud": "other-service"},
        {"sub": "another-user"},
        {"auth_level": True},
        {"iat": "not-a-time"},
        {"token_type": "approved_action"},
    ],
)
def test_modified_token_claims_do_not_authorize_reads(api_env, patch):
    token = issue_access_token(
        api_env.users["patient"], auth_level=1, settings=api_env.settings
    )
    claims = decode_access_token(token, api_env.settings)
    modified = jwt.encode(
        {**claims, **patch}, api_env.settings.jwt_secret, algorithm="HS256"
    )
    assert (
        api_env.client.get(
            "/api/me", headers={"Authorization": "Bearer " + modified}
        ).status_code
        == 401
    )


@pytest.mark.parametrize("mode", ["unsigned", "wrong_key", "future_iat", "missing_exp"])
def test_signature_time_and_required_claim_checks(api_env, mode):
    token = issue_access_token(
        api_env.users["patient"], auth_level=1, settings=api_env.settings
    )
    claims = decode_access_token(token, api_env.settings)
    if mode == "unsigned":
        token = jwt.encode(claims, key="", algorithm="none")
    elif mode == "wrong_key":
        token = jwt.encode(
            claims,
            key="synthetic-wrong-signing-key-more-than-thirty-two-characters",
            algorithm="HS256",
        )
    else:
        if mode == "future_iat":
            claims["iat"] = datetime.now(timezone.utc) + timedelta(minutes=1)
        else:
            claims.pop("exp")
        token = jwt.encode(claims, api_env.settings.jwt_secret, algorithm="HS256")
    assert (
        api_env.client.get(
            "/api/me", headers={"Authorization": "Bearer " + token}
        ).status_code
        == 401
    )


def test_registration_race_has_one_account_and_one_committed_audit_record(api_env):
    def register(email):
        return api_env.client.post(
            "/api/auth/register",
            json={
                "full_name": "Synthetic Concurrent Patient",
                "email": email,
                "password": "Synthetic-concurrency-password-2026!",
            },
        )

    with ThreadPoolExecutor(2) as pool:
        responses = list(
            pool.map(register, ["concurrent@example.com", "CONCURRENT@example.com"])
        )
    assert sorted(r.status_code for r in responses) == [201, 409]
    with connection(api_env.settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT user_id::text FROM users WHERE lower(email)='concurrent@example.com'"
            )
            rows = cursor.fetchall()
            assert len(rows) == 1
            cursor.execute(
                "SELECT count(*) FROM audit_logs WHERE resource_type='users' AND resource_id=%s",
                (rows[0][0],),
            )
            assert cursor.fetchone()[0] == 1


def test_read_filter_injection_is_parameterized_and_ids_are_validated(api_env):
    response = api_env.client.get(
        "/api/audit",
        headers=api_env.headers("auditor"),
        params={"actor_user_id": "' OR 1=1 --"},
    )
    assert response.status_code == 200 and response.json()["data"]["items"] == []
    assert (
        api_env.client.get(
            "/api/workflows/not-a-uuid", headers=api_env.headers()
        ).status_code
        == 422
    )
    response = api_env.client.get(
        "/api/me",
        headers={**api_env.headers(), "X-Request-ID": "untrusted-correlation-value"},
    )
    assert response.headers["X-Request-ID"] != "untrusted-correlation-value"
    assert response.headers["X-Request-ID"] == response.json()["request_id"]


def test_list_fetches_steps_in_two_queries_and_preserves_scope_and_order(
    api_env, monkeypatch
):
    ids = [str(uuid4()) for _ in range(20)]
    with connection(api_env.settings) as conn:
        with conn.cursor() as cursor:
            execute_values(
                cursor,
                "INSERT INTO workflow_runs(id,owner_user_id,workflow_type,request) VALUES %s",
                [
                    (
                        identifier,
                        api_env.users["patient" if i < 10 else "patient2"]["user_id"],
                        "onboarding",
                        "Synthetic page",
                    )
                    for i, identifier in enumerate(ids)
                ],
            )
            execute_values(
                cursor,
                "INSERT INTO workflow_steps(id,workflow_id,position,action,risk) VALUES %s",
                [
                    (str(uuid4()), identifier, position, "ehr.get_patient", "low")
                    for identifier in ids
                    for position in (2, 0, 1)
                ],
            )
    original = workflow_service.connection
    statements = []

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def execute(self, statement, args=None):
            statements.append(statement)
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

    monkeypatch.setattr(workflow_service, "connection", tracked)
    rows = workflow_service.list_workflows(
        api_env.settings, api_env.users["patient"], limit=50
    )
    assert len(rows) == 10 and len(statements) == 2
    assert all(row.owner_user_id == api_env.users["patient"]["user_id"] for row in rows)
    assert all([step.position for step in row.steps] == [0, 1, 2] for row in rows)
    assert len({str(step.id) for row in rows for step in row.steps}) == 30
    statements.clear()
    assert (
        workflow_service.list_workflows(
            api_env.settings, api_env.users["patient"], offset=100
        )
        == []
    )
    assert len(statements) == 1


@pytest.mark.parametrize(
    "status,payload,expected",
    [
        (200, {"data": {"status": "ready"}}, True),
        (503, {"error": {"code": "SERVICE_UNAVAILABLE"}}, False),
        (200, {"data": {"status": "ok"}}, False),
        (302, {"data": {"status": "ready"}}, False),
        (200, [], False),
        (200, "invalid-json", False),
        (200, "x" * 5000, False),
    ],
)
def test_container_probe_requires_valid_readiness_response(status, payload, expected):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.end_headers()
            content = payload if isinstance(payload, str) else json.dumps(payload)
            self.wfile.write(content.encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    try:
        assert healthcheck.check(server.server_port, timeout=1) == expected
    finally:
        server.shutdown()
        thread.join(2)
        server.server_close()


def test_benchmark_requires_explicit_admin_dsn_before_any_database_access(
    monkeypatch, capsys
):
    monkeypatch.delenv("MEDILINK_BENCHMARK_ADMIN_DSN", raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("Benchmark touched a database without explicit opt-in")

    monkeypatch.setattr(benchmark.psycopg2, "connect", forbidden)
    with pytest.raises(SystemExit) as exc:
        benchmark.main([])
    assert exc.value.code == 2
    assert "MEDILINK_BENCHMARK_ADMIN_DSN" in capsys.readouterr().err
