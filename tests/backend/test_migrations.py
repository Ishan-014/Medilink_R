"""Opt-in integration tests. Use ONLY a disposable empty PostgreSQL database."""

import os
from uuid import uuid4

import psycopg2
import pytest
from fastapi.testclient import TestClient

from backend.application import create_app
from backend.core.config import Settings
from backend.db.migrate import migrate, migration_files, MIGRATIONS_DIR


def test_duplicate_migration_versions_are_rejected(tmp_path):
    (tmp_path / "0001_one.sql").write_text("SELECT 1;")
    (tmp_path / "0001_two.sql").write_text("SELECT 2;")
    with pytest.raises(ValueError, match="Duplicate"):
        migration_files(tmp_path)


@pytest.fixture
def database():
    dsn = os.getenv("MEDILINK_TEST_DB_DSN")
    if not dsn:
        pytest.skip("Set MEDILINK_TEST_DB_DSN to a disposable empty database")
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
            if cursor.fetchone()[0]:
                pytest.fail("Migration tests require an empty database; refusing to modify existing tables")
        yield conn
    finally:
        # Every integration test is rolled back, including all DDL.
        conn.rollback()
        conn.close()


def test_migration_applies_once_and_status_does_not_change_schema(database):
    names = [p.name for p in migration_files()]
    assert migrate(database, status_only=True) == [(name, "pending") for name in names]
    with database.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.schema_migrations')")
        assert cursor.fetchone()[0] is None
    assert migrate(database) == [(name, "applied") for name in names]
    assert migrate(database) == [(name, "applied") for name in names]
    with database.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM schema_migrations")
        assert cursor.fetchone()[0] == len(names)


def test_applied_migration_cannot_be_edited(database, tmp_path):
    migrate(database)
    for source in MIGRATIONS_DIR.glob("*.sql"):
        (tmp_path / source.name).write_bytes(source.read_bytes() + b"\n-- changed\n")
    with pytest.raises(ValueError, match="modified"):
        migrate(database, directory=tmp_path)


def test_applied_migration_cannot_disappear(database, tmp_path):
    migrate(database)
    with pytest.raises(ValueError, match="missing"):
        migrate(database, directory=tmp_path)


def test_out_of_order_migration_is_rejected(database, tmp_path):
    (tmp_path / "0002_later.sql").write_text("SELECT 1;")
    migrate(database, directory=tmp_path)
    (tmp_path / "0001_earlier.sql").write_text("SELECT 2;")
    with pytest.raises(ValueError, match="Out-of-order"):
        migrate(database, directory=tmp_path)


def test_readiness_with_real_database(database):
    params = database.get_dsn_parameters()
    app = create_app(Settings(
        db_name=params["dbname"], db_host=params["host"],
        db_port=int(params["port"]), db_user=params["user"],
        db_password=psycopg2.extensions.parse_dsn(os.environ["MEDILINK_TEST_DB_DSN"]).get("password"),
    ))
    response = TestClient(app).get("/api/health/ready")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ready"


def test_failing_migration_batch_rolls_back_all_tables(database, tmp_path):
    (tmp_path / "0001_valid.sql").write_text("CREATE TABLE migration_rollback_probe (id INT);")
    (tmp_path / "0002_invalid.sql").write_text("SELECT missing_column FROM missing_table;")
    with pytest.raises(psycopg2.Error):
        with database:
            migrate(database, directory=tmp_path)
    with database.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.migration_rollback_probe'), to_regclass('public.schema_migrations')")
        assert cursor.fetchone() == (None, None)


def test_workflow_foreign_keys_and_constraints(database):
    migrate(database)
    workflow_id = str(uuid4())
    with database.cursor() as cursor:
        cursor.execute("""
            INSERT INTO workflow_runs (id, owner_user_id, workflow_type, request)
            VALUES (%s, 'staff-1', 'onboarding', 'Register a synthetic patient')
        """, (workflow_id,))
        cursor.execute("""
            INSERT INTO workflow_steps (id, workflow_id, position, action)
            VALUES (%s, %s, 0, 'validate_patient')
        """, (str(uuid4()), workflow_id))
        invalid_statements = [
            ("UPDATE workflow_runs SET status = 'INVALID' WHERE id = %s", (workflow_id,)),
            ("UPDATE workflow_runs SET workflow_type = 'INVALID' WHERE id = %s", (workflow_id,)),
            ("UPDATE workflow_runs SET request = ' ' WHERE id = %s", (workflow_id,)),
            ("UPDATE workflow_steps SET risk = 'INVALID' WHERE workflow_id = %s", (workflow_id,)),
            ("INSERT INTO workflow_steps (id, workflow_id, position, action) VALUES (%s, %s, 0, 'duplicate')",
             (str(uuid4()), workflow_id)),
            ("INSERT INTO workflow_steps (id, workflow_id, position, action) VALUES (%s, %s, 0, 'orphan')",
             (str(uuid4()), str(uuid4()))),
        ]
        for sql, args in invalid_statements:
            cursor.execute("SAVEPOINT invalid_write")
            with pytest.raises(psycopg2.IntegrityError):
                cursor.execute(sql, args)
            cursor.execute("ROLLBACK TO SAVEPOINT invalid_write")


def test_existing_patient_and_user_tables_are_untouched(database):
    with database.cursor() as cursor:
        cursor.execute("""CREATE TABLE users (user_id SERIAL PRIMARY KEY, full_name TEXT, email TEXT UNIQUE,
            password_hash TEXT, role TEXT CHECK (role IN ('doctor','receptionist')),
            is_active BOOLEAN DEFAULT TRUE, legacy_marker TEXT)""")
        cursor.execute("""INSERT INTO users (full_name,email,password_hash,role,legacy_marker)
            VALUES ('Existing doctor','existing@example.com','existing-hash','doctor','existing-user')""")
        cursor.execute("CREATE TABLE patients (legacy_marker TEXT)")
        cursor.execute("INSERT INTO patients VALUES ('existing-patient')")
    migrate(database)
    with database.cursor() as cursor:
        cursor.execute("SELECT legacy_marker FROM users")
        assert cursor.fetchall() == [("existing-user",)]
        cursor.execute("SELECT * FROM patients")
        assert cursor.fetchall() == [("existing-patient",)]
        # The older role-only restriction was expanded without changing accounts.
        cursor.execute("""INSERT INTO users (full_name,email,password_hash,role)
            VALUES ('Patient','new@example.com','new-hash','patient')""")


def test_upgrade_from_part1_retains_workflow_rows(database, tmp_path):
    name = "0001_workflow_foundation.sql"
    (tmp_path / name).write_bytes((MIGRATIONS_DIR / name).read_bytes())
    migrate(database, directory=tmp_path)
    workflow_id = str(uuid4())
    with database.cursor() as cursor:
        cursor.execute("""INSERT INTO workflow_runs (id,owner_user_id,workflow_type,request)
            VALUES (%s,'existing-owner','onboarding','Existing request')""", (workflow_id,))
    migrate(database)
    with database.cursor() as cursor:
        cursor.execute("SELECT owner_user_id,status,version FROM workflow_runs WHERE id = %s", (workflow_id,))
        assert cursor.fetchone() == ("existing-owner", "CREATED", 0)


def test_incompatible_legacy_identity_schema_fails_without_partial_migration(database):
    with database.cursor() as cursor:
        cursor.execute("CREATE TABLE users (legacy_marker TEXT)")
        cursor.execute("INSERT INTO users VALUES ('preserve-me')")
        cursor.execute("SAVEPOINT before_upgrade")
        with pytest.raises(psycopg2.errors.RaiseException):
            migrate(database)
        cursor.execute("ROLLBACK TO SAVEPOINT before_upgrade")
        cursor.execute("SELECT legacy_marker FROM users")
        assert cursor.fetchall() == [("preserve-me",)]
        cursor.execute("SELECT to_regclass('public.workflow_runs')")
        assert cursor.fetchone()[0] is None


def test_part3_upgrade_preserves_part2_data_and_enforces_document_links(database, tmp_path):
    for name in ("0001_workflow_foundation.sql", "0002_auth_and_workflows.sql"):
        (tmp_path / name).write_bytes((MIGRATIONS_DIR / name).read_bytes())
    migrate(database, directory=tmp_path)
    workflow_id = str(uuid4())
    with database.cursor() as cursor:
        cursor.execute("INSERT INTO users (full_name,email,password_hash,role) VALUES ('Existing','existing@example.com','existing-hash','patient')")
        cursor.execute("INSERT INTO workflow_runs (id,owner_user_id,workflow_type,request) VALUES (%s,'1','onboarding','Existing request')", (workflow_id,))
    migrate(database)
    with database.cursor() as cursor:
        cursor.execute("SELECT password_hash FROM users")
        assert cursor.fetchone()[0] == "existing-hash"
        cursor.execute("SELECT request,document_ids FROM workflow_runs WHERE id = %s", (workflow_id,))
        assert cursor.fetchone() == ("Existing request", [])
        cursor.execute("SAVEPOINT invalid_document_link")
        with pytest.raises(psycopg2.IntegrityError):
            cursor.execute("INSERT INTO workflow_documents (workflow_id,document_id) VALUES (%s,%s)", (workflow_id, str(uuid4())))
        cursor.execute("ROLLBACK TO SAVEPOINT invalid_document_link")


def test_part3_refuses_unmanaged_document_references(database, tmp_path):
    for name in ("0001_workflow_foundation.sql", "0002_auth_and_workflows.sql"):
        (tmp_path / name).write_bytes((MIGRATIONS_DIR / name).read_bytes())
    migrate(database, directory=tmp_path)
    with database.cursor() as cursor:
        cursor.execute("""INSERT INTO workflow_runs (id,owner_user_id,workflow_type,request,document_ids)
            VALUES (%s,'legacy','onboarding','Unmanaged reference','["unknown-document"]')""", (str(uuid4()),))
        cursor.execute("SAVEPOINT before_document_upgrade")
        with pytest.raises(psycopg2.errors.RaiseException):
            migrate(database)
        cursor.execute("ROLLBACK TO SAVEPOINT before_document_upgrade")
        cursor.execute("SELECT to_regclass('public.documents')")
        assert cursor.fetchone()[0] is None


def test_part4_upgrade_preserves_data_and_rejects_cross_workflow_calls(database, tmp_path):
    for name in ("0001_workflow_foundation.sql", "0002_auth_and_workflows.sql", "0003_documents.sql"):
        (tmp_path / name).write_bytes((MIGRATIONS_DIR / name).read_bytes())
    migrate(database, directory=tmp_path)
    first, second, step = str(uuid4()), str(uuid4()), str(uuid4())
    with database.cursor() as cursor:
        for workflow_id in (first, second):
            cursor.execute("INSERT INTO workflow_runs (id,owner_user_id,workflow_type,request) VALUES (%s,'existing','onboarding','Existing request')", (workflow_id,))
        cursor.execute("INSERT INTO workflow_steps (id,workflow_id,position,action) VALUES (%s,%s,0,'ehr.get_patient')", (step, first))
    migrate(database)
    with database.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM workflow_runs")
        assert cursor.fetchone()[0] == 2
        cursor.execute("SAVEPOINT invalid_call")
        with pytest.raises(psycopg2.IntegrityError):
            cursor.execute("""INSERT INTO tool_calls (id,workflow_id,workflow_step_id,actor_user_id,server,tool,request_fingerprint)
                VALUES (%s,%s,%s,'existing','ehr','get_patient',%s)""", (str(uuid4()), second, step, "a" * 64))
        cursor.execute("ROLLBACK TO SAVEPOINT invalid_call")


def test_part5_upgrade_keeps_legacy_patients_and_prior_workflows(database, tmp_path):
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name < "0005":
            (tmp_path / path.name).write_bytes(path.read_bytes())
    migrate(database, directory=tmp_path)
    workflow_id = str(uuid4())
    with database.cursor() as cursor:
        cursor.execute("CREATE TABLE patients (legacy_marker TEXT)")
        cursor.execute("INSERT INTO patients VALUES ('original-EHR-record')")
        cursor.execute("INSERT INTO workflow_runs (id,owner_user_id,workflow_type,request) VALUES (%s,'existing','onboarding','Existing request')", (workflow_id,))
    migrate(database)
    with database.cursor() as cursor:
        cursor.execute("SELECT * FROM patients")
        assert cursor.fetchall() == [("original-EHR-record",)]
        cursor.execute("SELECT request,managed_kind,subject_user_id FROM workflow_runs WHERE id=%s", (workflow_id,))
        assert cursor.fetchone() == ("Existing request", None, None)
