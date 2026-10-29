"""
backend/auth/router.py

Wires the 3-layer login pipeline into FastAPI endpoints.

    POST /auth/login/credentials  -> stage2 token
    POST /auth/login/id-card      -> stage3 token
    POST /auth/login/face         -> final access token
"""

import os
import jwt
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from tempfile import NamedTemporaryFile


from .models import Employee, LoginResult, LoginStage
from .credentials import login_with_credentials, verify_stage_token
from .security import issue_access_token, STAFF_ROLES
from .stages import consume_stage
from .id_card import verify_id_card_scan
from .employee_store import (
    get_employee_by_username,
    get_employee_by_id,
    init_db,
)

def verify_face(stage_token, path):
    # Keep optional biometric models out of API imports and migration/test startup.
    from .facial_recognition import verify_face as verify
    return verify(stage_token, path)


router = APIRouter(prefix="/auth", tags=["auth"])

init_db()


# ---------------------------------------------------------------------------
# Employee lookup
# ---------------------------------------------------------------------------
def _lookup_employee_by_username(username: str) -> Employee | None:
    return get_employee_by_username(username)


# ---------------------------------------------------------------------------
# Final access token
# ---------------------------------------------------------------------------
def _valid_stage(token, expected):
    try:
        stage = verify_stage_token(token, expected)
    except (jwt.InvalidTokenError, ValueError, KeyError):
        raise HTTPException(401, "Invalid or expired authentication stage")
    employee = get_employee_by_id(stage.employee_id)
    if employee is None or employee.role not in STAFF_ROLES:
        raise HTTPException(401, "Active staff account required")
    return stage


def _issue_access_token(employee_id: str) -> str:
    employee = get_employee_by_id(employee_id)

    if employee is None:
        raise HTTPException(
            status_code=401,
            detail="Employee not found.",
        )

    if employee.role not in STAFF_ROLES:
        raise HTTPException(403, "Staff account required")
    return issue_access_token({
        "user_id": employee.employee_id, "email": employee.username, "role": employee.role,
    }, auth_level=3)


# ---------------------------------------------------------------------------
# Stage 1: credentials
# ---------------------------------------------------------------------------
@router.post("/login/credentials", response_model=LoginResult)
def login_credentials(
    username: str = Form(...),
    password: str = Form(...),
):
    result = login_with_credentials(
        username,
        password,
        _lookup_employee_by_username,
    )

    if not result.success:
        raise HTTPException(
            status_code=401,
            detail=result.message,
        )

    return result


# ---------------------------------------------------------------------------
# Stage 2: ID card QR scan
# ---------------------------------------------------------------------------
@router.post("/login/id-card", response_model=LoginResult)
def login_id_card(
    stage2_token: str = Form(...),
    qr_string: str = Form(...),
):
    _valid_stage(stage2_token, LoginStage.STAGE2_ID_CARD)
    result = verify_id_card_scan(
        stage2_token,
        qr_string,
    )

    if not result.success:
        raise HTTPException(
            status_code=401,
            detail=result.message,
        )

    consume_stage(stage2_token)
    return result


# ---------------------------------------------------------------------------
# Stage 2: QR scanner frame
# ---------------------------------------------------------------------------
@router.post("/login/id-card/scan-frame")
def scan_id_card_frame(
    stage2_token: str = Form(...),
    frame: UploadFile = File(...),
):
    """
    Decode a QR from a live webcam frame.

    Returning qr_string=null simply means no QR was detected
    in this particular frame yet.
    """

    _valid_stage(stage2_token, LoginStage.STAGE2_ID_CARD)
    import cv2
    import numpy as np
    raw = frame.file.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(413, "Camera frame is too large")

    image = cv2.imdecode(
        np.frombuffer(raw, dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )

    if image is None:
        return {"qr_string": None}

    detector = cv2.QRCodeDetector()

    qr_string, _, _ = detector.detectAndDecode(image)

    if not qr_string:
        return {"qr_string": None}

    return {"qr_string": qr_string}


# ---------------------------------------------------------------------------
# Stage 3: facial recognition
# ---------------------------------------------------------------------------
@router.post("/login/face", response_model=LoginResult)
def login_face(
    stage3_token: str = Form(...),
    frame: UploadFile = File(...),
):
    stage = _valid_stage(stage3_token, LoginStage.STAGE3_FACIAL)
    content = frame.file.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(413, "Camera frame is too large")
    with NamedTemporaryFile(
        suffix=".jpg",
        delete=False,
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = verify_face(
            stage3_token,
            tmp_path,
        )
    finally:
        os.remove(tmp_path)

    if not result.success:
        raise HTTPException(
            status_code=401,
            detail=result.message,
        )

    # All 3 authentication layers passed.
    # verify_face() already populated employee_id.
    if result.employee_id != stage.employee_id:
        raise HTTPException(401, "Authentication identity mismatch")
    consume_stage(stage3_token)
    access_token = _issue_access_token(result.employee_id)

    result.token = access_token

    return result