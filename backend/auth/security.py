"""Shared access tokens, live identity checks, and server-side RBAC."""

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg2.extras import RealDictCursor
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

from backend.api.schemas import CurrentUser, Role
from backend.core.config import Settings, get_settings
from backend.db.connection import connection
from backend.observability.context import request_context

PASSWORD_HASH = PasswordHash.recommended()
DUMMY_HASH = PASSWORD_HASH.hash("unused-timing-comparison-password")
STAFF_ROLES = {Role.DOCTOR, Role.RECEPTIONIST, Role.ADMIN, Role.AUDITOR}
bearer = HTTPBearer(auto_error=False)


def settings_for_request(request: Request) -> Settings:
    return request.app.state.settings


def signing_key(settings: Settings | None = None) -> str:
    key = (settings or get_settings()).jwt_secret
    if not key or len(key) < 32 or key.startswith(("dev-", "replace-")):
        raise HTTPException(503, "Configure a random MEDILINK_JWT_SECRET of at least 32 characters")
    return key


def verify_password(password: str, hashed: str) -> bool:
    try:
        return PASSWORD_HASH.verify(password, hashed)
    except (UnknownHashError, ValueError):
        return False


def issue_access_token(user: dict, *, auth_level: int, settings: Settings | None = None) -> str:
    config = settings or get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode({
        "sub": str(user["user_id"]), "user_id": str(user["user_id"]),
        "employee_id": str(user["user_id"]), "email": user["email"],
        "role": user["role"], "auth_level": auth_level, "token_type": "access",
        "iss": "medilink", "aud": "medilink-api", "iat": now,
        "exp": now + timedelta(minutes=config.access_token_minutes),
    }, signing_key(config), algorithm="HS256")


def unauthorized():
    return HTTPException(401, "Invalid or expired access token", headers={"WWW-Authenticate": "Bearer"})


def decode_access_token(token: str, settings: Settings) -> dict:
    try:
        payload = jwt.decode(
            token, signing_key(settings), algorithms=["HS256"],
            audience="medilink-api", issuer="medilink",
            options={"require": ["exp", "iat", "sub", "user_id", "auth_level", "token_type"]},
        )
        if (payload["token_type"] != "access" or payload["sub"] != payload["user_id"]
                or not isinstance(payload["user_id"], str) or not payload["user_id"]
                or type(payload["auth_level"]) is not int or payload["auth_level"] not in (1, 3)):
            raise unauthorized()
        return payload
    except jwt.InvalidTokenError as exc:
        raise unauthorized() from exc


def current_identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(settings_for_request),
) -> dict:
    if credentials is None:
        raise unauthorized()
    payload = decode_access_token(credentials.credentials, settings)
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT user_id::text AS user_id, full_name, email, role, is_active
                FROM public.users WHERE user_id::text = %s
            """, (payload["user_id"],))
            user = cursor.fetchone()
    if not user or not user["is_active"] or user["role"] not in {r.value for r in Role}:
        raise unauthorized()
    if user["role"] in STAFF_ROLES and payload["auth_level"] != 3:
        raise HTTPException(403, "Complete all three staff authentication steps")
    context = request_context.get()
    if context:
        context.actor_user_id = user["user_id"]
    return {**dict(user), "auth_level": payload["auth_level"], "employee_id": user["user_id"]}


def require_roles(*roles: Role):
    def authorize(user: dict = Depends(current_identity)):
        if user["role"] not in roles:
            raise HTTPException(403, "Insufficient permissions")
        return user
    return authorize


def public_user(user: dict) -> CurrentUser:
    return CurrentUser(user_id=str(user["user_id"]), email=user["email"], role=user["role"])
