"""Patient registration/login and privileged account provisioning."""

import psycopg2
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg2.extras import RealDictCursor
from pydantic import ConfigDict, EmailStr, Field, SecretStr, field_validator

from backend.api.schemas import Contract, CurrentUser, Role, SuccessResponse
from backend.auth.security import (
    PASSWORD_HASH, DUMMY_HASH, current_identity, issue_access_token,
    public_user, require_roles, settings_for_request, signing_key, verify_password,
)
from backend.core.config import Settings
from backend.db.connection import connection

router = APIRouter(prefix="/api", tags=["authentication"])


class PasswordInput(Contract):
    # Password whitespace must be preserved, unlike other contract strings.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    email: EmailStr = Field(max_length=254)
    password: SecretStr = Field(min_length=1, max_length=1024)


class Registration(PasswordInput):
    full_name: str = Field(min_length=1, max_length=200)
    password: SecretStr = Field(min_length=12, max_length=1024)

    @field_validator("full_name")
    @classmethod
    def valid_name(cls, value):
        if not value.strip():
            raise ValueError("Name cannot be blank")
        return value.strip()


class AccountCreate(Registration):
    role: Role


class TokenData(Contract):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: CurrentUser


def create_account(body: Registration, role: Role, settings: Settings) -> dict:
    hashed = PASSWORD_HASH.hash(body.password.get_secret_value())
    try:
        with connection(settings) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    INSERT INTO public.users (full_name, email, password_hash, role, is_active)
                    VALUES (%s, %s, %s, %s, TRUE)
                    RETURNING user_id::text AS user_id, full_name, email, role
                """, (body.full_name, str(body.email).lower(), hashed, role.value))
                return dict(cursor.fetchone())
    except psycopg2.errors.UniqueViolation as exc:
        raise HTTPException(409, "Account already exists") from exc


def password_login(body: PasswordInput, settings: Settings) -> TokenData:
    signing_key(settings)  # Fail before a successful password check if misconfigured.
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT user_id::text AS user_id, full_name, email, password_hash, role, is_active
                FROM public.users WHERE lower(email) = %s
            """, (str(body.email).lower(),))
            user = cursor.fetchone()
    valid = verify_password(body.password.get_secret_value(), user["password_hash"] if user else DUMMY_HASH)
    if not user or not valid or not user["is_active"]:
        raise HTTPException(401, "Invalid email or password", headers={"WWW-Authenticate": "Bearer"})
    if user["role"] != Role.PATIENT:
        raise HTTPException(403, "Staff must use /auth/login/credentials, /auth/login/id-card, and /auth/login/face")
    return TokenData(
        access_token=issue_access_token(user, auth_level=1, settings=settings),
        expires_in=settings.access_token_minutes * 60, user=public_user(user),
    )


@router.post("/auth/register", status_code=201, response_model=SuccessResponse[CurrentUser])
def register(body: Registration, request: Request, settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=public_user(create_account(body, Role.PATIENT, settings)), request_id=request.state.request_id)


@router.post("/auth/login", response_model=SuccessResponse[TokenData])
def login(body: PasswordInput, request: Request, settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=password_login(body, settings), request_id=request.state.request_id)


@router.get("/me", response_model=SuccessResponse[CurrentUser])
def me(request: Request, user: dict = Depends(current_identity)):
    return SuccessResponse(data=public_user(user), request_id=request.state.request_id)


@router.post("/admin/users", status_code=201, response_model=SuccessResponse[CurrentUser])
def provision(body: AccountCreate, request: Request,
              admin: dict = Depends(require_roles(Role.ADMIN)),
              settings: Settings = Depends(settings_for_request)):
    return SuccessResponse(data=public_user(create_account(body, body.role, settings)), request_id=request.state.request_id)
