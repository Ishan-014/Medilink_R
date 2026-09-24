from dotenv import load_dotenv
load_dotenv()

from fastapi import (
    FastAPI,
    HTTPException,
    Depends,
    UploadFile,
    File
)

from fastapi.security import (
    HTTPBearer,
    HTTPAuthorizationCredentials
)

from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel

from typing import Optional, List
from datetime import datetime, timedelta, timezone
from io import BytesIO

import psycopg2
import jwt
import os
import re

from pwdlib import PasswordHash
from pypdf import PdfReader

from backend.auth.router import router as auth_router
from backend.auth.credentials import JWT_SECRET, JWT_ALGORITHM
from backend.agents.graph import run_conversation
# ==========================================================
# FASTAPI APPLICATION
# ==========================================================

app = FastAPI(
    title="MediLink Backend",
    version="1.0"
)

# Three-layer staff authentication: credentials → ID card → face.
app.include_router(auth_router)


# ==========================================================
# CORS
# React frontend access
# ==========================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================================
# PASSWORD HASHING
# ==========================================================

password_hash = PasswordHash.recommended()


# ==========================================================
# JWT CONFIGURATION
# ==========================================================

SECRET_KEY = JWT_SECRET

ALGORITHM = "HS256"

TOKEN_EXPIRE_MINUTES = 60

security = HTTPBearer()


# ==========================================================
# TEMPORARY DOCUMENT STORAGE
#
# For prototype only.
# Documents disappear if backend restarts.
# ==========================================================

uploaded_documents = {}

MAX_FILE_SIZE = 5 * 1024 * 1024


# ==========================================================
# DATABASE CONNECTION
# ==========================================================

def get_db_connection():
    return psycopg2.connect(
        dbname=os.getenv("MEDILINK_DB_NAME", "medilink"),
        host=os.getenv("MEDILINK_DB_HOST", "localhost"),
        port=os.getenv("MEDILINK_DB_PORT", "5432"),
        user=os.getenv("MEDILINK_DB_USER", "postgres"),
        password=os.getenv("MEDILINK_DB_PASSWORD"),
    )


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

class LoginRequest(BaseModel):

    email: str
    password: str


class ChatRequest(BaseModel):

    message: str
    history: List[dict] = []


# ==========================================================
# JWT TOKEN CREATION
# ==========================================================

def create_access_token(
    user_id,
    email,
    role
):

    expiry = (
        datetime.now(timezone.utc)
        + timedelta(
            minutes=TOKEN_EXPIRE_MINUTES
        )
    )

    payload = {

        "user_id": user_id,

        "email": email,

        "role": role,

        "exp": expiry
    }

    return jwt.encode(
        payload,
        SECRET_KEY,
        algorithm=ALGORITHM
    )


# ==========================================================
# READ CURRENT USER FROM JWT
# ==========================================================

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    token = credentials.credentials

    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM]
        )

        return {
            "user_id": payload["user_id"],
            "employee_id": payload.get("employee_id", payload["user_id"]),
            "email": payload["email"],
            "role": payload["role"],
            "auth_level": payload.get("auth_level", 1),
        }

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Token expired"
        )

    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=401,
            detail="Invalid token"
        )


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
def login(
    data: LoginRequest
):

    conn = get_db_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            user_id,
            full_name,
            email,
            password_hash,
            role,
            is_active

        FROM users

        WHERE email = %s
        """,

        (data.email,)
    )

    user = cursor.fetchone()

    cursor.close()
    conn.close()


    if user is None:

        raise HTTPException(
            status_code=401,
            detail="Invalid email or password"
        )


    user_id = user[0]

    full_name = user[1]

    email = user[2]

    stored_password_hash = user[3]

    role = user[4]

    is_active = user[5]


    if not is_active:

        raise HTTPException(
            status_code=403,
            detail="User account disabled"
        )


    if not password_hash.verify(
        data.password,
        stored_password_hash
    ):

        raise HTTPException(
            status_code=401,
            detail="Invalid email or password"
        )


    access_token = create_access_token(
        user_id,
        email,
        role
    )


    return {

        "message":
            "Login successful",

        "access_token":
            access_token,

        "token_type":
            "bearer",

        "user": {

            "user_id":
                user_id,

            "full_name":
                full_name,

            "email":
                email,

            "role":
                role
        }
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
    current_user=Depends(get_current_user)
):
    message = (data.message or "").strip()

    if not message:
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty",
        )

    if current_user["role"] not in {"doctor", "receptionist"}:
        raise HTTPException(
            status_code=403,
            detail="Unsupported role",
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
async def upload_chat_document(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user)
):

    filename = (
        file.filename
        or "uploaded_file"
    )


    filename_lower = (
        filename.lower()
    )


    # ------------------------------------------------------
    # File type validation
    # ------------------------------------------------------

    if not (

        filename_lower.endswith(
            ".pdf"
        )

        or

        filename_lower.endswith(
            ".txt"
        )

    ):

        raise HTTPException(
            status_code=400,

            detail=(
                "Only PDF and TXT files "
                "are currently supported."
            )
        )


    # ------------------------------------------------------
    # Read file
    # ------------------------------------------------------

    contents = await file.read()


    # ------------------------------------------------------
    # File size check
    # ------------------------------------------------------

    if len(contents) > MAX_FILE_SIZE:

        raise HTTPException(
            status_code=413,

            detail=(
                "File is too large. "
                "Maximum size is 5 MB."
            )
        )


    extracted_text = ""


    # ======================================================
    # TXT EXTRACTION
    # ==========================================================

    if filename_lower.endswith(
        ".txt"
    ):

        try:

            extracted_text = (
                contents.decode(
                    "utf-8",
                    errors="ignore"
                )
            )

        except Exception:

            raise HTTPException(
                status_code=400,
                detail="Unable to read TXT file."
            )


    # ======================================================
    # PDF EXTRACTION
    # ==========================================================

    elif filename_lower.endswith(
        ".pdf"
    ):

        try:

            reader = PdfReader(
                BytesIO(contents)
            )


            pages_text = []


            for page in reader.pages:

                page_text = (
                    page.extract_text()
                    or ""
                )


                pages_text.append(
                    page_text
                )


            extracted_text = (
                "\n".join(
                    pages_text
                )
            )

        except Exception:

            raise HTTPException(
                status_code=400,
                detail="Unable to read PDF file."
            )


    # ------------------------------------------------------
    # Clean extracted text
    # ------------------------------------------------------

    extracted_text = (
        extracted_text.strip()
    )


    if not extracted_text:

        raise HTTPException(
            status_code=400,

            detail=(
                "No readable text was found. "
                "The PDF may be scanned or image-based."
            )
        )


    # ------------------------------------------------------
    # Store against logged-in user
    # ------------------------------------------------------

    uploaded_documents[
        current_user["user_id"]
    ] = {

        "filename":
            filename,

        "text":
            extracted_text[:50000],

        "role":
            current_user["role"]
    }


    # ------------------------------------------------------
    # Return result
    # ------------------------------------------------------

    return {

        "message":
            "Document uploaded and processed successfully.",

        "filename":
            filename,

        "file_type":
            (
                "PDF"

                if filename_lower.endswith(
                    ".pdf"
                )

                else "TXT"
            ),

        "characters_extracted":
            len(extracted_text),

        "preview":
            extracted_text[:500]

    }