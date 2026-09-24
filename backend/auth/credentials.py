"""
backend/auth/credentials.py

Layer 1 of the login pipeline: username + password.
On success, issues a short-lived JWT "stage token" that proves the caller
passed this layer -- it must be presented to layer 2 (ID card) next.
"""

import os
import uuid
import jwt
from datetime import datetime, timedelta, timezone
from pwdlib import PasswordHash
from .models import Employee, StageToken, LoginStage, LoginResult

password_hash = PasswordHash.recommended()
# In production load this from a secrets manager / env var, never hardcode.
JWT_SECRET = os.environ.get("MEDILINK_JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"
STAGE_TOKEN_TTL_MINUTES = 5   # each stage token is short-lived on purpose


# ---------------------------------------------------------------------------
# Password helpers (used at employee-provisioning time too)
# ---------------------------------------------------------------------------
def hash_password(plain_password: str) -> str:
    return password_hash.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)


# ---------------------------------------------------------------------------
# Stage token issuance / verification (shared helper used by all 3 layers)
# ---------------------------------------------------------------------------
def issue_stage_token(employee_id: str, stage: LoginStage, nonce: str) -> str:
    payload = {
        "employee_id": employee_id,
        "stage": stage.value,
        "nonce": nonce,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=STAGE_TOKEN_TTL_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_stage_token(token: str, expected_stage: LoginStage) -> StageToken:
    """Raises jwt exceptions on failure -- caller should catch and return a
    clean LoginResult(success=False, ...) rather than leaking stack traces."""
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    if payload["stage"] != expected_stage.value:
        raise ValueError(f"expected stage {expected_stage.value}, got {payload['stage']}")
    return StageToken(employee_id=payload["employee_id"], stage=payload["stage"], nonce=payload["nonce"])


# ---------------------------------------------------------------------------
# Layer 1 entry point
# ---------------------------------------------------------------------------
def login_with_credentials(username: str, password: str, employee_lookup) -> LoginResult:
    """
    employee_lookup: callable(username) -> Optional[Employee]
    (kept as a callable rather than a direct DB import so this module has
    no DB dependency -- wire your real repository/service in at the router.)
    """
    employee = employee_lookup(username)
    if employee is None or not verify_password(password, employee.hashed_password):
        return LoginResult(success=False, stage=LoginStage.STAGE1_CREDENTIALS,
                            message="Invalid username or password.")

    nonce = str(uuid.uuid4())
    token = issue_stage_token(employee.employee_id, LoginStage.STAGE2_ID_CARD, nonce)
    return LoginResult(
        success=True,
        stage=LoginStage.STAGE2_ID_CARD,   # tells the client which stage to do next
        message="Credentials verified. Please scan your ID card.",
        token=token,
    )
