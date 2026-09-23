"""
backend/auth/router.py

Wires the 3-layer login pipeline into FastAPI endpoints. The client calls
these in strict order -- each returns a short-lived stage token that must be
passed to the next call:

    POST /auth/login/credentials  -> stage2 token
    POST /auth/login/id-card      -> stage3 token
    POST /auth/login/face         -> final access token

RBAC TODO: `role` already exists on Employee (models.py) but is not checked
anywhere here. When you implement RBAC, add a dependency like
`require_role("staff")` on the protected routes elsewhere in the app --
do NOT bolt it onto this auth flow itself, since login should succeed the
same way regardless of role; authorization belongs on the resource routes.
"""

import os
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from tempfile import NamedTemporaryFile

import cv2
import numpy as np

from .models import Employee, LoginResult, LoginStage
from .credentials import login_with_credentials, JWT_SECRET, JWT_ALGORITHM
from .id_card import verify_id_card_scan
from .facial_recognition import verify_face
from .employee_store import get_employee_by_username, get_employee_by_id, init_db

router = APIRouter(prefix="/auth", tags=["auth"])

ACCESS_TOKEN_TTL_HOURS = 8

init_db()   # no-op if the table already exists; safe to call on every import


# ---------------------------------------------------------------------------
# Backed by the minimal SQLite store in employee_store.py for now.
# Swap this one line for a real DB call once the team settles on Postgres/etc
# -- nothing else in this file needs to change.
# ---------------------------------------------------------------------------
def _lookup_employee_by_username(username: str) -> Employee | None:
    return get_employee_by_username(username)


def _issue_access_token(employee_id: str) -> str:
    """Issue the application-compatible MediLink JWT after all 3 layers pass."""
    employee = get_employee_by_id(employee_id)
    if employee is None:
        raise HTTPException(status_code=401, detail="Authenticated staff account is no longer active.")

    if employee.role not in {"doctor", "receptionist"}:
        raise HTTPException(status_code=403, detail="Staff account has no supported MediLink role.")

    payload = {
        "user_id": employee.employee_id,
        "employee_id": employee.employee_id,
        "email": employee.username,
        "full_name": employee.full_name,
        "role": employee.role,
        "auth_level": 3,
        "exp": datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


# ---------------------------------------------------------------------------
# Stage 1: credentials
# ---------------------------------------------------------------------------
@router.post("/login/credentials", response_model=LoginResult)
def login_credentials(username: str = Form(...), password: str = Form(...)):
    result = login_with_credentials(username, password, _lookup_employee_by_username)
    if not result.success:
        raise HTTPException(status_code=401, detail=result.message)
    return result


# ---------------------------------------------------------------------------
# Stage 2: ID card QR scan
# ---------------------------------------------------------------------------
@router.post("/login/id-card", response_model=LoginResult)
def login_id_card(stage2_token: str = Form(...), qr_string: str = Form(...)):
    result = verify_id_card_scan(stage2_token, qr_string)
    if not result.success:
        raise HTTPException(status_code=401, detail=result.message)
    return result


@router.post("/login/id-card/scan-frame")
def scan_id_card_frame(stage2_token: str = Form(...), frame: UploadFile = File(...)):
    """Decode a QR from a live webcam frame. The browser only captures video;
    OpenCV decodes the QR and the existing signed-card verifier remains
    authoritative. Returning 200 with qr_string=null simply means no QR was
    found in this frame yet.
    """
    raw = frame.file.read()
    image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return {"qr_string": None}

    detector = cv2.QRCodeDetector()
    qr_string, _, _ = detector.detectAndDecode(image)
    if not qr_string:
        return {"qr_string": None}

    return {"qr_string": qr_string}


# ---------------------------------------------------------------------------
# Stage 3: facial recognition -- accepts an uploaded frame from the browser/kiosk
# ---------------------------------------------------------------------------
@router.post("/login/face", response_model=LoginResult)
def login_face(stage3_token: str = Form(...), frame: UploadFile = File(...)):
    with NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp.write(frame.file.read())
        tmp_path = tmp.name

    try:
        result = verify_face(stage3_token, tmp_path)
    finally:
        os.remove(tmp_path)

    if not result.success:
        raise HTTPException(status_code=401, detail=result.message)

    # All 3 layers passed. Use the employee_id verify_face() already captured
    # (before its slow model call) rather than re-decoding stage3_token here --
    # that token can expire during a cold model load, even though the actual
    # verification already succeeded moments earlier.
    access_token = _issue_access_token(result.employee_id)
    result.token = access_token
    return result