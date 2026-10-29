"""API tests use only an explicitly selected disposable empty PostgreSQL DB."""

import os
from dataclasses import dataclass

import psycopg2
import pytest
from fastapi.testclient import TestClient
from psycopg2.extras import RealDictCursor

from backend.application import create_app
from backend.auth.security import PASSWORD_HASH, issue_access_token
from backend.core.config import Settings, get_settings
from backend.db.connection import connection
from backend.db.migrate import migrate

TEST_PASSWORD = "Synthetic-password-2026!"
TEST_SECRET = "part2-isolated-test-secret-with-more-than-32-characters"
TEST_HASH = PASSWORD_HASH.hash(TEST_PASSWORD)
TABLES = ("audit_logs", "system_events", "external_reconciliations", "approval_events", "workflow_graph_queries", "appointments", "workflow_case_evidence", "workflow_cases", "claims", "patient_profiles",
          "tool_calls", "approvals", "workflow_documents", "document_extraction_results", "documents",
          "consumed_auth_stages", "workflow_action_keys", "workflow_creation_keys",
          "workflow_steps", "workflow_runs", "users", "schema_migrations")


@dataclass
class APIEnvironment:
    settings: Settings
    client: TestClient
    users: dict

    def headers(self, role="patient", *, level=None):
        user = self.users[role]
        token = issue_access_token(user, auth_level=level if level is not None else (1 if role.startswith("patient") else 3),
                                   settings=self.settings)
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def api_env(monkeypatch, tmp_path):
    dsn = os.getenv("MEDILINK_TEST_DB_DSN")
    if not dsn:
        pytest.skip("Set MEDILINK_TEST_DB_DSN to an empty disposable database")
    params = psycopg2.extensions.parse_dsn(dsn)
    config = Settings(
        db_name=params.get("dbname", "postgres"), db_host=params.get("host", "localhost"),
        db_port=int(params.get("port", "5432")), db_user=params.get("user", "postgres"),
        db_password=params.get("password"), jwt_secret=TEST_SECRET,
        document_storage_dir=tmp_path / "documents",
    )
    for name, value in {
        "MEDILINK_DB_NAME": config.db_name, "MEDILINK_DB_HOST": config.db_host,
        "MEDILINK_DB_PORT": str(config.db_port), "MEDILINK_DB_USER": config.db_user,
        "MEDILINK_DB_PASSWORD": config.db_password or "", "MEDILINK_JWT_SECRET": TEST_SECRET,
        "MEDILINK_CARD_SECRET": "part2-isolated-card-secret-with-more-than-32-characters",
        "MEDILINK_DOCUMENT_STORAGE_DIR": str(config.document_storage_dir),
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    with connection(config) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
            if cursor.fetchone()[0]:
                pytest.fail("API tests require an empty disposable database; refusing existing data")
        migrate(conn)
    users = {}
    try:
        with connection(config) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                for label in ("patient", "patient2", "doctor", "receptionist", "admin", "auditor"):
                    role = "patient" if label == "patient2" else label
                    cursor.execute("""
                        INSERT INTO users (full_name, email, password_hash, role)
                        VALUES (%s, %s, %s, %s)
                        RETURNING user_id::text AS user_id, full_name, email, role
                    """, (label, f"{label}@example.com", TEST_HASH, role))
                    users[label] = dict(cursor.fetchone())
        with TestClient(create_app(config)) as client:
            yield APIEnvironment(config, client, users)
    finally:
        with connection(config) as conn:
            with conn.cursor() as cursor:
                cursor.execute("ALTER TABLE approvals DROP CONSTRAINT approvals_consumed_call_id_fkey")
                for name in TABLES:
                    cursor.execute(f"DROP TABLE public.{name}")
                cursor.execute("DROP FUNCTION prevent_approval_event_changes()")
                cursor.execute("DROP FUNCTION record_backend_change()")
                cursor.execute("DROP FUNCTION prevent_trace_changes()")
                cursor.execute("DROP FUNCTION backend_audit_metadata(jsonb,jsonb)")
        get_settings.cache_clear()
