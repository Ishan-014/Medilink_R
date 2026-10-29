from dotenv import load_dotenv
load_dotenv()

from fastapi import (
    HTTPException,
    Depends,
    UploadFile,
    File
)

from pydantic import BaseModel

from typing import Optional, List

import re


from backend.application import create_app
from backend.db.connection import connect

from backend.auth.security import current_identity, settings_for_request, require_roles
from backend.api.schemas import Role
from backend.api.auth import PasswordInput, password_login
from backend.core.config import Settings
from backend.services.legacy_document_service import upload_chat_document as persist_chat_document
from backend.agents.graph import run_conversation
# ==========================================================
# FASTAPI APPLICATION
# ==========================================================

app = create_app()



# ==========================================================
# DATABASE CONNECTION
# ==========================================================

def get_db_connection():
    return connect()


def normalize_patient_record(patient):
    if not patient:
        return None

    normalized = dict(patient)

    if "patient_id" not in normalized and "id" in normalized:
        normalized["patient_id"] = normalized["id"]
    if "id" not in normalized and "patient_id" in normalized:
        normalized["id"] = normalized["patient_id"]

    if "first_name" not in normalized or "last_name" not in normalized:
        raw_name = str(normalized.get("name") or normalized.get("first_name") or "").strip()
        if raw_name:
            parts = raw_name.split()
            normalized["first_name"] = parts[0] if parts else ""
            normalized["last_name"] = " ".join(parts[1:]) if len(parts) > 1 else ""
        else:
            normalized["first_name"] = normalized.get("first_name") or ""
            normalized["last_name"] = normalized.get("last_name") or ""

    if "department" not in normalized:
        normalized["department"] = normalized.get("department") or "Clinical"

    return normalized


# ==========================================================
# REQUEST MODELS
# ==========================================================

class LoginRequest(PasswordInput):
    pass


class ChatRequest(BaseModel):

    message: str
    history: List[dict] = []


# All protected legacy routes now share the live identity and auth-stage checks.
def get_current_user(current_user=Depends(current_identity)):
    return current_user


# ==========================================================
# RBAC HELPERS
# ==========================================================

def doctor_only(
    current_user=Depends(get_current_user)
):

    if (
        current_user["role"]
        != "doctor"
    ):

        raise HTTPException(
            status_code=403,
            detail="Doctor access required"
        )

    return current_user


def receptionist_only(
    current_user=Depends(get_current_user)
):

    if (
        current_user["role"]
        != "receptionist"
    ):

        raise HTTPException(
            status_code=403,
            detail="Receptionist access required"
        )

    return current_user


# ==========================================================
# HOME
# ==========================================================

@app.get("/")
def home():

    return {
        "message":
            "MediLink backend is running"
    }


# ==========================================================
# LOGIN
# ==========================================================

@app.post("/login")
def login(data: LoginRequest, settings: Settings = Depends(settings_for_request)):
    # Retain the legacy response shape, but never bypass staff QR/face checks.
    result = password_login(data, settings)
    return {
        "message": "Login successful", "access_token": result.access_token,
        "token_type": result.token_type, "user": result.user.model_dump(),
    }


# ==========================================================
# CURRENT USER
# ==========================================================

@app.get("/me")
def get_me(
    current_user=Depends(get_current_user)
):

    return current_user


# ==========================================================
# DOCTOR PATIENT LIST + SEARCH
# ==========================================================

@app.get("/doctor/patients")
def doctor_patients(
    q: Optional[str] = None,
    current_user=Depends(doctor_only)
):
    conn = get_db_connection()
    cursor = conn.cursor()

    if q:
        search = f"%{q}%"

        cursor.execute(
            """
            SELECT *
            FROM patients
            WHERE id ILIKE %s
               OR name ILIKE %s
               OR phone ILIKE %s
               OR email ILIKE %s
               OR blood_group ILIKE %s
            ORDER BY id
            LIMIT 100
            """,
            (
                search,
                search,
                search,
                search,
                search
            )
        )

    else:
        cursor.execute(
            """
            SELECT *
            FROM patients
            ORDER BY id
            LIMIT 100
            """
        )

    rows = cursor.fetchall()

    columns = [
        description[0]
        for description in cursor.description
    ]

    patients = [
        normalize_patient_record(dict(zip(columns, row)))
        for row in rows
    ]

    cursor.close()
    conn.close()

    return {
        "logged_in_as": current_user["role"],
        "patients": patients
    }


# ==========================================================
# RECEPTIONIST PATIENT LIST + SEARCH
# ==========================================================

@app.get("/receptionist/patients")
def receptionist_patients(
    q: Optional[str] = None,
    current_user=Depends(receptionist_only)
):

    conn = get_db_connection()

    cursor = conn.cursor()


    if q:

        search = f"%{q}%"

        cursor.execute(
            """
            SELECT *

            FROM receptionist_patient_view

            WHERE patient_id ILIKE %s
               OR first_name ILIKE %s
               OR last_name ILIKE %s
               OR assigned_doctor ILIKE %s
               OR department ILIKE %s

            ORDER BY patient_id

            LIMIT 100
            """,

            (
                search,
                search,
                search,
                search,
                search
            )
        )

    else:

        cursor.execute(
            """
            SELECT *

            FROM receptionist_patient_view

            ORDER BY patient_id

            LIMIT 100
            """
        )


    rows = cursor.fetchall()

    columns = [
        description[0]
        for description
        in cursor.description
    ]


    patients = [
        normalize_patient_record(dict(zip(columns, row)))
        for row in rows
    ]

    cursor.close()
    conn.close()


    return {

        "logged_in_as":
            current_user["role"],

        "patients":
            patients
    }


# ==========================================================
# DOCTOR INDIVIDUAL PATIENT
# ==========================================================

@app.get(
    "/doctor/patient/{patient_id}"
)
def doctor_patient_detail(
    patient_id: str,
    current_user=Depends(doctor_only)
):

    conn = get_db_connection()

    cursor = conn.cursor()


    cursor.execute(
        """
        SELECT *
        FROM patients
        WHERE id = %s
        LIMIT 1
        """,
        (patient_id,)
    )

    row = cursor.fetchone()

    if row is None:
        cursor.close()
        conn.close()
        raise HTTPException(
            status_code=404,
            detail="Patient not found"
        )

    columns = [
        description[0]
        for description in cursor.description
    ]

    patient = normalize_patient_record(dict(zip(columns, row)))

    cursor.close()
    conn.close()

    return patient


# ==========================================================
# RECEPTIONIST INDIVIDUAL PATIENT
# ==========================================================

@app.get(
    "/receptionist/patient/{patient_id}"
)
def receptionist_patient_detail(
    patient_id: str,
    current_user=Depends(receptionist_only)
):

    conn = get_db_connection()

    cursor = conn.cursor()


    cursor.execute(
        """
        SELECT *
        FROM receptionist_patient_view
        WHERE patient_id = %s
        LIMIT 1
        """,
        (patient_id,)
    )

    row = cursor.fetchone()

    if row is None:
        cursor.close()
        conn.close()
        raise HTTPException(
            status_code=404,
            detail="Patient not found"
        )

    columns = [
        description[0]
        for description in cursor.description
    ]

    patient = normalize_patient_record(dict(zip(columns, row)))

    cursor.close()
    conn.close()

    return patient


# ==========================================================
# DOCUMENT HELPER
# Simple document summary
# ==========================================================

def simple_document_summary(
    text,
    max_sentences=5
):

    clean_text = " ".join(
        text.split()
    )


    sentences = re.split(
        r"(?<=[.!?])\s+",
        clean_text
    )


    useful_sentences = [

        sentence.strip()

        for sentence
        in sentences

        if sentence.strip()
    ]


    summary = " ".join(
        useful_sentences[
            :max_sentences
        ]
    )


    if not summary:

        summary = clean_text[:1000]


    return summary


# ==========================================================
# DOCUMENT HELPER
# Find relevant document sentences
# ==========================================================

def find_relevant_document_text(
    document_text,
    question
):

    clean_text = " ".join(
        document_text.split()
    )


    sentences = re.split(
        r"(?<=[.!?])\s+",
        clean_text
    )


    ignored_words = {
        "what",
        "when",
        "where",
        "which",
        "this",
        "that",
        "does",
        "document",
        "uploaded",
        "file",
        "about",
        "from",
        "have",
        "with",
        "please",
        "tell"
    }


    question_words = {

        word.lower()

        for word in re.findall(
            r"[A-Za-z0-9]+",
            question
        )

        if (
            len(word) > 3
            and word.lower()
            not in ignored_words
        )
    }


    scored_sentences = []


    for sentence in sentences:

        sentence_words = {

            word.lower()

            for word in re.findall(
                r"[A-Za-z0-9]+",
                sentence
            )
        }


        score = len(
            question_words.intersection(
                sentence_words
            )
        )


        if score > 0:

            scored_sentences.append(
                (
                    score,
                    sentence
                )
            )


    scored_sentences.sort(
        reverse=True,
        key=lambda item: item[0]
    )


    relevant = [

        sentence

        for score, sentence
        in scored_sentences[:5]

    ]


    if relevant:

        return " ".join(
            relevant
        )


    return (
        "I could not find a clearly matching "
        "section in the uploaded document."
    )


# ==========================================================
# DOCUMENT HELPER
# Detect clinical-looking document
# ==========================================================

def document_looks_clinical(
    text
):

    clinical_keywords = [

        "diagnosis",
        "diagnosed",
        "medication",
        "medicine",
        "prescription",
        "allergy",
        "allergies",
        "laboratory",
        "lab result",
        "clinical",
        "treatment",
        "symptom",
        "blood pressure",
        "medical history",
        "patient history"

    ]


    lower_text = text.lower()


    return any(

        keyword in lower_text

        for keyword
        in clinical_keywords

    )


# ==========================================================
# ROLE-AWARE CHATBOT
# EHR + uploaded document support
# ==========================================================

@app.post("/chat")
def chatbot(
    data: ChatRequest,
    current_user=Depends(doctor_only)
):
    message = (data.message or "").strip()

    if not message:
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty",
        )

    try:
        response_text = run_conversation(message, history=data.history)
        return {"response": response_text}
    except HTTPException:
        raise
    except Exception as exc:
        import logging
        logging.exception("Chat agent failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to process the request right now. Please try again later.",
        ) from exc


# ==========================================================
# DOCUMENT UPLOAD
# PDF + TXT
# ==========================================================

@app.post("/chat/upload")
def upload_chat_document(
    file: UploadFile = File(...),
    current_user=Depends(require_roles(Role.DOCTOR, Role.RECEPTIONIST)),
    settings: Settings = Depends(settings_for_request),
):
    return persist_chat_document(settings, file, current_user)
