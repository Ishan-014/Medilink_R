import importlib
import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType

import jwt
import pytest
from fastapi.testclient import TestClient

from backend.auth import router as staff
from backend.auth.credentials import issue_stage_token
from backend.auth.id_card import _encode_card_payload
from backend.auth.models import LoginResult, LoginStage
from backend.auth.security import PASSWORD_HASH, decode_access_token, issue_access_token
from backend.core.config import Settings
from backend.db.connection import connection

PASSWORD = "Synthetic-password-2026!"


def test_patient_registration_login_and_me(api_env):
    client = api_env.client
    body = {"full_name": " New Patient ", "email": "NEW@example.com", "password": " whitespace-password "}
    registered = client.post("/api/auth/register", json=body)
    assert registered.status_code == 201
    assert registered.json()["data"]["role"] == "patient"
    with connection(api_env.settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT password_hash, full_name FROM users WHERE email = 'new@example.com'")
            hashed, name = cursor.fetchone()
            assert hashed.startswith("$argon2")
            assert PASSWORD_HASH.verify(body["password"], hashed)
            assert name == "New Patient"
    login = client.post("/api/auth/login", json={"email": "new@example.com", "password": body["password"]})
    assert login.status_code == 200
    data = login.json()["data"]
    assert data["expires_in"] == 3600
    assert data["token_type"] == "bearer"
    me = client.get("/api/me", headers={"Authorization": f"Bearer {data['access_token']}"})
    assert me.status_code == 200
    assert me.json()["data"]["user_id"] == registered.json()["data"]["user_id"]
    assert "password" not in me.text


def test_public_registration_cannot_grant_roles_or_duplicate_email(api_env):
    body = {"full_name": "Attempt", "email": "new@example.com", "password": PASSWORD, "role": "admin"}
    assert api_env.client.post("/api/auth/register", json=body).status_code == 422
    body.pop("role")
    body["email"] = "PATIENT@example.com"
    assert api_env.client.post("/api/auth/register", json=body).status_code == 409


@pytest.mark.parametrize("password", ["short", "x" * 1025])
def test_new_password_bounds(api_env, password):
    response = api_env.client.post("/api/auth/register", json={
        "full_name": "Patient", "email": "new@example.com", "password": password,
    })
    assert response.status_code == 422


@pytest.mark.parametrize("role", ["patient", "doctor", "receptionist", "admin", "auditor"])
def test_all_roles_can_access_me_with_appropriate_completed_auth(api_env, role):
    response = api_env.client.get("/api/me", headers=api_env.headers(role))
    assert response.status_code == 200
    assert response.json()["data"]["role"] == role


@pytest.mark.parametrize("role", ["doctor", "receptionist", "admin", "auditor"])
def test_password_only_login_and_token_cannot_bypass_staff_stages(api_env, role):
    response = api_env.client.post("/api/auth/login", json={"email": api_env.users[role]["email"], "password": PASSWORD})
    assert response.status_code == 403
    assert api_env.client.get("/api/me", headers=api_env.headers(role, level=1)).status_code == 403


def test_wrong_password_unknown_user_and_disabled_account(api_env):
    client = api_env.client
    for email in ("patient@example.com", "missing@example.com"):
        response = client.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
        assert response.status_code == 401
    headers = api_env.headers()
    with connection(api_env.settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE users SET is_active = FALSE WHERE role = 'patient'")
    assert client.get("/api/me", headers=headers).status_code == 401
    assert client.post("/api/auth/login", json={"email": "patient@example.com", "password": PASSWORD}).status_code == 401


def test_current_database_role_overrides_old_token_role(api_env):
    headers = api_env.headers("admin")
    with connection(api_env.settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE users SET role = 'auditor' WHERE role = 'admin'")
    assert api_env.client.get("/api/me", headers=headers).json()["data"]["role"] == "auditor"
    response = api_env.client.post("/api/admin/users", headers=headers, json={
        "full_name": "New doctor", "email": "new@example.com", "password": PASSWORD, "role": "doctor",
    })
    assert response.status_code == 403


@pytest.mark.parametrize("role", ["patient", "doctor", "receptionist", "auditor", "admin"])
def test_only_admin_can_provision_privileged_accounts(api_env, role):
    response = api_env.client.post("/api/admin/users", headers=api_env.headers(role), json={
        "full_name": "New doctor", "email": "new@example.com", "password": PASSWORD, "role": "doctor",
    })
    assert response.status_code == (201 if role == "admin" else 403)


def test_missing_malformed_expired_wrong_audience_and_stage_tokens(api_env):
    client = api_env.client
    assert client.get("/api/me").status_code == 401
    good = issue_access_token(api_env.users["patient"], auth_level=1, settings=api_env.settings)
    claims = decode_access_token(good, api_env.settings)
    stage = issue_stage_token(api_env.users["doctor"]["user_id"], LoginStage.STAGE2_ID_CARD, "test-nonce")
    tokens = ["garbage", stage]
    for patch in [
        {"exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
        {"aud": "wrong-audience"}, {"token_type": "stage"}, {"user_id": None},
    ]:
        tokens.append(jwt.encode({**claims, **patch}, api_env.settings.jwt_secret, algorithm="HS256"))
    for token in tokens:
        response = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"


def test_missing_secret_fails_closed_without_breaking_health(api_env):
    from backend.application import create_app
    app = create_app(Settings())
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/auth/login", json={"email": "patient@example.com", "password": PASSWORD}).status_code == 503


def test_staff_three_step_flow_replay_and_final_token(api_env, monkeypatch):
    client = api_env.client
    first = client.post("/auth/login/credentials", data={"username": "DOCTOR@example.com", "password": PASSWORD})
    assert first.status_code == 200
    stage2 = first.json()["token"]
    assert client.get("/api/me", headers={"Authorization": f"Bearer {stage2}"}).status_code == 401
    doctor_id = api_env.users["doctor"]["user_id"]
    qr = _encode_card_payload(doctor_id)
    second = client.post("/auth/login/id-card", data={"stage2_token": stage2, "qr_string": qr})
    assert second.status_code == 200
    assert client.post("/auth/login/id-card", data={"stage2_token": stage2, "qr_string": qr}).status_code == 401
    monkeypatch.setattr(staff, "verify_face", lambda token, path: LoginResult(
        success=True, stage=LoginStage.COMPLETE, message="Synthetic face match", employee_id=doctor_id,
    ))
    stage3 = second.json()["token"]
    third = client.post("/auth/login/face", data={"stage3_token": stage3}, files={"frame": ("face.jpg", b"synthetic", "image/jpeg")})
    assert third.status_code == 200
    assert client.get("/api/me", headers={"Authorization": f"Bearer {third.json()['token']}"}).status_code == 200
    replay = client.post("/auth/login/face", data={"stage3_token": stage3}, files={"frame": ("face.jpg", b"synthetic", "image/jpeg")})
    assert replay.status_code == 401


def test_wrong_card_and_account_disabled_between_login_stages(api_env):
    client = api_env.client
    doctor_id = api_env.users["doctor"]["user_id"]
    stage2 = client.post("/auth/login/credentials", data={"username": "doctor@example.com", "password": PASSWORD}).json()["token"]
    wrong = client.post("/auth/login/id-card", data={
        "stage2_token": stage2, "qr_string": _encode_card_payload(api_env.users["receptionist"]["user_id"]),
    })
    assert wrong.status_code == 401
    with connection(api_env.settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE users SET is_active = FALSE WHERE user_id::text = %s", (doctor_id,))
    assert client.post("/auth/login/id-card", data={"stage2_token": stage2, "qr_string": _encode_card_payload(doctor_id)}).status_code == 401


def test_legacy_login_bypass_is_closed_and_me_shape_remains(api_env, monkeypatch):
    # Stub only the unchanged agent import; no AI calls or biometric model needed.
    graph = ModuleType("backend.agents.graph")
    graph.run_conversation = lambda *args, **kwargs: "unused"
    monkeypatch.setitem(sys.modules, "backend.agents.graph", graph)
    sys.modules.pop("backend.main", None)
    main = importlib.import_module("backend.main")
    client = TestClient(main.app)
    assert client.post("/login", json={"email": "doctor@example.com", "password": PASSWORD}).status_code == 403
    me = client.get("/me", headers=api_env.headers("doctor"))
    assert me.status_code == 200
    assert me.json()["role"] == "doctor"
    assert "data" not in me.json()
    # The unchanged agent exposes clinical read/write tools without role context.
    assert client.post("/chat", headers=api_env.headers("receptionist"), json={"message": "Read clinical record"}).status_code == 403
    assert client.get("/doctor/patients", headers=api_env.headers("receptionist")).status_code == 403
    assert client.post("/chat/upload", headers=api_env.headers("auditor"),
                       files={"file": ("notes.txt", b"synthetic", "text/plain")}).status_code == 403
    with connection(api_env.settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute("CREATE VIEW receptionist_patient_view AS SELECT 'PAT-001'::text AS patient_id, 'Synthetic'::text AS first_name")
    try:
        response = client.get("/receptionist/patient/PAT-001", headers=api_env.headers("receptionist"))
        assert response.status_code == 200
        assert response.json()["patient_id"] == "PAT-001"
        assert "conditions" not in response.json()
    finally:
        with connection(api_env.settings) as conn:
            with conn.cursor() as cursor:
                cursor.execute("DROP VIEW receptionist_patient_view")
    sys.modules.pop("backend.main", None)
