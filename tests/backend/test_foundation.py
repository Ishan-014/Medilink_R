"""Part 1 checks. No AI, MCP, biometric dependencies, or existing EHR required."""

from contextlib import contextmanager

import psycopg2
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.api import health
from backend.api.schemas import WorkflowCreate
from backend.application import create_app
from backend.core.config import Settings
from backend.db import connection as db


@pytest.fixture
def app():
    return create_app(Settings())


def test_liveness_does_not_connect_to_database(app, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Liveness must not connect to PostgreSQL")
    monkeypatch.setattr(health, "connection", forbidden)
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ok", "service": "medilink-backend"}
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_readiness_failure_is_sanitized(app, monkeypatch):
    @contextmanager
    def unavailable(*args, **kwargs):
        raise psycopg2.OperationalError("private database password")
        yield  # pragma: no cover
    monkeypatch.setattr(health, "connection", unavailable)
    response = TestClient(app).get("/api/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "private" not in response.text


def test_api_errors_and_legacy_errors_keep_separate_contracts(app):
    @app.get("/legacy-test")
    def legacy():
        raise HTTPException(401, "Legacy login required", headers={"WWW-Authenticate": "Bearer"})

    client = TestClient(app)
    assert client.get("/api/missing").json()["error"]["code"] == "NOT_FOUND"
    response = client.get("/legacy-test")
    assert response.json() == {"detail": "Legacy login required"}
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_validation_never_echoes_submitted_input(app):
    @app.post("/api/contract-test")
    def contract(body: WorkflowCreate):
        return body

    response = TestClient(app).post("/api/contract-test", json={
        "workflow_type": "secret-invalid-value", "request": "Patient private information",
    })
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["details"]
    assert "secret-invalid-value" not in response.text
    assert "Patient private information" not in response.text


def test_unexpected_exception_does_not_disclose_data(app):
    @app.get("/api/crash-test")
    def crash():
        raise RuntimeError("secret database details")

    response = TestClient(app, raise_server_exceptions=False).get("/api/crash-test")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.headers["X-Request-ID"] == response.json()["request_id"]
    assert "secret" not in response.text


def test_cors_still_allows_existing_frontend(app):
    response = TestClient(app).options("/api/health", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "GET",
    })
    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"


def test_config_reads_existing_environment_names(monkeypatch):
    monkeypatch.setenv("MEDILINK_DB_PORT", "55432")
    monkeypatch.setenv("MEDILINK_DB_PASSWORD", "test-private-password")
    monkeypatch.setenv("MEDILINK_CORS_ORIGINS", "https://example.test, https://staff.test")
    config = Settings.from_env()
    assert config.db_port == 55432
    assert config.db_password == "test-private-password"
    assert config.cors_origins == ("https://example.test", "https://staff.test")
    assert "test-private-password" not in repr(config)


@pytest.mark.parametrize("kwargs", [{"db_port": 0}, {"db_connect_timeout": 0}, {"db_statement_timeout_ms": 0}])
def test_config_rejects_invalid_port_and_timeouts(kwargs):
    with pytest.raises(ValueError):
        Settings(**kwargs)


@pytest.mark.parametrize("request_text", ["", "   ", "x" * 10001])
def test_workflow_contract_rejects_empty_or_oversized_request(request_text):
    with pytest.raises(ValidationError):
        WorkflowCreate(workflow_type="onboarding", request=request_text)


def test_workflow_contract_cannot_set_owner_status_or_approval():
    with pytest.raises(ValidationError):
        WorkflowCreate(workflow_type="onboarding", request="Register a patient", owner_user_id="another-user")


@pytest.mark.parametrize("should_fail", [False, True])
def test_connection_closes_and_uses_transaction_context(monkeypatch, should_fail):
    events = []

    class FakeConnection:
        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, exc_type, exc, tb):
            events.append("rollback" if exc_type else "commit")

        def close(self):
            events.append("close")

    monkeypatch.setattr(db, "connect", lambda settings: FakeConnection())
    try:
        with db.connection(Settings()):
            if should_fail:
                raise ValueError("test rollback")
    except ValueError:
        pass
    assert events == ["enter", "rollback" if should_fail else "commit", "close"]


def test_openapi_lists_health_and_error_schemas(app):
    schema = app.openapi()
    assert "/api/health" in schema["paths"]
    assert "503" in schema["paths"]["/api/health/ready"]["get"]["responses"]
