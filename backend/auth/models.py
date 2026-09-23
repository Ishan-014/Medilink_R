"""
backend/auth/models.py

Shared data models for the 3-layer MediLink auth pipeline:
  Layer 1: credentials (username + password)
  Layer 2: ID card (QR code, HMAC-signed)
  Layer 3: facial recognition (face embedding match)

RBAC is intentionally NOT enforced yet -- `role` is stored but unused.
See router.py bottom for the TODO marking where to plug it in.
"""

from pydantic import BaseModel
from typing import Optional
from enum import Enum


class Employee(BaseModel):
    employee_id: str
    username: str
    hashed_password: str
    full_name: str
    role: Optional[str] = "unassigned"     # RBAC placeholder -- not enforced yet
    face_encoding_path: Optional[str] = None  # path to stored reference embedding


class LoginStage(str, Enum):
    STAGE1_CREDENTIALS = "stage1_credentials"
    STAGE2_ID_CARD = "stage2_id_card"
    STAGE3_FACIAL = "stage3_facial"
    COMPLETE = "complete"


class StageToken(BaseModel):
    """Short-lived token issued after each stage passes, carried to the next."""
    employee_id: str
    stage: LoginStage
    nonce: str          # replay-protection value, unique per login attempt


class LoginResult(BaseModel):
    success: bool
    stage: LoginStage
    message: str
    token: Optional[str] = None   # JWT for this stage, or final access token on COMPLETE
    employee_id: Optional[str] = None  # set on COMPLETE so callers don't need to re-decode a token