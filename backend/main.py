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

from typing import Optional
from datetime import datetime, timedelta, timezone
from io import BytesIO

import psycopg2
import jwt
import re

from pwdlib import PasswordHash
from pypdf import PdfReader


# ==========================================================
# FASTAPI APPLICATION
# ==========================================================

app = FastAPI(
    title="MediLink Backend",
    version="1.0"
)


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

SECRET_KEY = "medilink-demo-secret-key"

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
        dbname="medilink"
    )


# ==========================================================
# REQUEST MODELS
# ==========================================================

class LoginRequest(BaseModel):

    email: str
    password: str


class ChatRequest(BaseModel):

    message: str


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
    credentials: HTTPAuthorizationCredentials
    = Depends(security)
):

    token = credentials.credentials

    try:

        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM]
        )

        return {

            "user_id":
                payload["user_id"],

            "email":
                payload["email"],

            "role":
                payload["role"]

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

            FROM doctor_patient_view

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

            FROM doctor_patient_view

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

        dict(
            zip(
                columns,
                row
            )
        )

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

        dict(
            zip(
                columns,
                row
            )
        )

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

        FROM doctor_patient_view

        WHERE patient_id = %s
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
        for description
        in cursor.description
    ]


    patient = dict(
        zip(
            columns,
            row
        )
    )


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
        for description
        in cursor.description
    ]


    patient = dict(
        zip(
            columns,
            row
        )
    )


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

    message = data.message.strip()

    role = current_user["role"]

    user_id = current_user["user_id"]


    if not message:

        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty"
        )


    lower_message = message.lower()


    # ======================================================
    # CURRENT USER DOCUMENT
    # ==========================================================

    document = uploaded_documents.get(
        user_id
    )


    # ======================================================
    # DETECT PATIENT ID
    # ==========================================================

    patient_match = re.search(
        r"\bMED\d{5}\b",
        message.upper()
    )


    patient_id = (

        patient_match.group(0)

        if patient_match

        else None

    )


    # ======================================================
    # DETECT DOCUMENT QUESTION
    # ==========================================================

    document_words = [

        "document",
        "file",
        "pdf",
        "txt",
        "uploaded",
        "upload"

    ]


    document_actions = [

        "summarize",
        "summary",
        "what does it say",
        "what does this say",
        "what is in it",
        "explain it"

    ]


    asking_about_document = (

        any(
            word in lower_message
            for word in document_words
        )

        or

        (
            document is not None

            and patient_id is None

            and any(
                phrase in lower_message
                for phrase
                in document_actions
            )
        )

    )


    # ======================================================
    # DOCUMENT QUESTIONS
    # ==========================================================

    if asking_about_document:


        if document is None:

            return {

                "reply":
                    "You have not uploaded a PDF or TXT document yet."

            }


        document_text = document["text"]

        filename = document["filename"]


        # --------------------------------------------------
        # Receptionist cannot access clinical document
        # --------------------------------------------------

        if (
            role == "receptionist"

            and

            document_looks_clinical(
                document_text
            )
        ):

            raise HTTPException(
                status_code=403,

                detail=(
                    "This document appears to contain clinical "
                    "information. Receptionists are not authorized "
                    "to access clinical document content."
                )
            )


        # --------------------------------------------------
        # Ask filename
        # --------------------------------------------------

        if (
            "what file" in lower_message
            or
            "which file" in lower_message
            or
            "filename" in lower_message
        ):

            return {

                "reply":
                    f"The currently loaded document is {filename}."

            }


        # --------------------------------------------------
        # Summarize
        # --------------------------------------------------

        if (
            "summarize" in lower_message
            or
            "summary" in lower_message
            or
            "what does" in lower_message
            or
            "what is in" in lower_message
            or
            "explain" in lower_message
        ):

            summary = (
                simple_document_summary(
                    document_text
                )
            )


            return {

                "reply":
                    f"Summary of {filename}: {summary}"

            }


        # --------------------------------------------------
        # Search within document
        # --------------------------------------------------

        relevant_text = (
            find_relevant_document_text(
                document_text,
                message
            )
        )


        return {

            "reply":
                f"From {filename}: {relevant_text}"

        }


    # ======================================================
    # NO PATIENT ID
    # ==========================================================

    if patient_id is None:


        if role == "doctor":

            response = (

                "I can retrieve clinical information from "
                "the MediLink EHR. Include a patient ID "
                "such as MED00001. You can ask about "
                "diagnosis, medication, allergies, labs, "
                "blood group or last visit."

            )


            if document:

                response += (

                    f" You currently have "
                    f"{document['filename']} loaded. "
                    "You can ask me to summarize or search "
                    "the uploaded document."

                )


            return {
                "reply":
                    response
            }


        if role == "receptionist":

            response = (

                "I can retrieve administrative patient "
                "information such as appointments, insurance, "
                "contact details, doctor or department. "
                "Include a patient ID such as MED00001."

            )


            if document:

                response += (

                    f" You currently have "
                    f"{document['filename']} loaded."

                )


            return {
                "reply":
                    response
            }


        raise HTTPException(
            status_code=403,
            detail="Unsupported role"
        )


    # ======================================================
    # DOCTOR CHAT
    # ==========================================================

    if role == "doctor":

        conn = get_db_connection()

        cursor = conn.cursor()


        cursor.execute(
            """
            SELECT *

            FROM doctor_patient_view

            WHERE patient_id = %s
            """,

            (patient_id,)
        )


        row = cursor.fetchone()


        if row is None:

            cursor.close()
            conn.close()

            return {

                "reply":
                    f"No patient was found with ID {patient_id}."

            }


        columns = [

            description[0]

            for description
            in cursor.description

        ]


        patient = dict(
            zip(
                columns,
                row
            )
        )


        cursor.close()
        conn.close()


        # Diagnosis

        if (
            "diagnosis" in lower_message
            or
            "condition" in lower_message
        ):

            return {

                "reply":
                    f"{patient_id}'s recorded diagnosis is "
                    f"{patient['diagnosis_summary']}."

            }


        # Medication

        if (
            "medication" in lower_message
            or
            "medicine" in lower_message
            or
            "prescription" in lower_message
        ):

            return {

                "reply":
                    f"{patient_id}'s recorded medication is "
                    f"{patient['medications']}."

            }


        # Allergies

        if (
            "allergy" in lower_message
            or
            "allergies" in lower_message
        ):

            return {

                "reply":
                    f"{patient_id}'s recorded allergies are "
                    f"{patient['allergies']}."

            }


        # Labs

        if (
            "lab" in lower_message
            or
            "test" in lower_message
        ):

            return {

                "reply":
                    f"The latest lab summary for {patient_id} is "
                    f"{patient['lab_summary']}."

            }


        # Blood group

        if (
            "blood group" in lower_message
            or
            "blood type" in lower_message
        ):

            return {

                "reply":
                    f"{patient_id}'s blood group is "
                    f"{patient['blood_group']}."

            }


        # Last visit

        if "visit" in lower_message:

            return {

                "reply":
                    f"{patient_id}'s last recorded visit was "
                    f"{patient['last_visit']}."

            }


        # Doctor / department

        if (
            "doctor" in lower_message
            or
            "department" in lower_message
        ):

            return {

                "reply":
                    f"{patient_id} is assigned to "
                    f"{patient['assigned_doctor']} "
                    f"in {patient['department']}."

            }


        # General clinical summary

        return {

            "reply":

                f"Patient "
                f"{patient['first_name']} "
                f"{patient['last_name']} "
                f"({patient_id}). "

                f"Blood group: "
                f"{patient['blood_group']}. "

                f"Department: "
                f"{patient['department']}. "

                f"Assigned doctor: "
                f"{patient['assigned_doctor']}. "

                f"Diagnosis: "
                f"{patient['diagnosis_summary']}. "

                f"Medication: "
                f"{patient['medications']}. "

                f"Allergies: "
                f"{patient['allergies']}. "

                f"Lab summary: "
                f"{patient['lab_summary']}. "

                f"Last visit: "
                f"{patient['last_visit']}."

        }


    # ======================================================
    # RECEPTIONIST CHAT
    # ==========================================================

    if role == "receptionist":


        # --------------------------------------------------
        # Block clinical requests
        # --------------------------------------------------

        clinical_words = [

            "diagnosis",
            "condition",
            "medication",
            "medicine",
            "prescription",
            "allergy",
            "allergies",
            "lab",
            "test result",
            "clinical",
            "treatment"

        ]


        if any(

            word in lower_message

            for word
            in clinical_words

        ):

            raise HTTPException(
                status_code=403,

                detail=(
                    "Receptionists are not authorized "
                    "to access clinical patient information."
                )
            )


        conn = get_db_connection()

        cursor = conn.cursor()


        cursor.execute(
            """
            SELECT *

            FROM receptionist_patient_view

            WHERE patient_id = %s
            """,

            (patient_id,)
        )


        row = cursor.fetchone()


        if row is None:

            cursor.close()
            conn.close()

            return {

                "reply":
                    f"No patient was found with ID {patient_id}."

            }


        columns = [

            description[0]

            for description
            in cursor.description

        ]


        patient = dict(
            zip(
                columns,
                row
            )
        )


        cursor.close()
        conn.close()


        # Appointment

        if (
            "appointment" in lower_message
            or
            "schedule" in lower_message
        ):

            return {

                "reply":
                    f"{patient_id}'s next appointment is "
                    f"{patient['next_appointment']} with "
                    f"{patient['assigned_doctor']} in "
                    f"{patient['department']}. "
                    f"Status: "
                    f"{patient['appointment_status']}."

            }


        # Insurance

        if (
            "insurance" in lower_message
            or
            "policy" in lower_message
        ):

            return {

                "reply":
                    f"{patient_id} is registered with "
                    f"{patient['insurance_provider']}. "
                    f"Policy number: "
                    f"{patient['policy_number']}."

            }


        # Phone

        if (
            "phone" in lower_message
            or
            "contact" in lower_message
        ):

            return {

                "reply":
                    f"{patient['first_name']} "
                    f"{patient['last_name']}'s registered "
                    f"phone number is "
                    f"{patient['phone']}."

            }


        # Email

        if "email" in lower_message:

            return {

                "reply":
                    f"{patient['first_name']} "
                    f"{patient['last_name']}'s registered "
                    f"email is {patient['email']}."

            }


        # Address

        if "address" in lower_message:

            return {

                "reply":
                    f"{patient['first_name']} "
                    f"{patient['last_name']}'s registered "
                    f"address is {patient['address']}."

            }


        # Doctor / department

        if (
            "doctor" in lower_message
            or
            "department" in lower_message
        ):

            return {

                "reply":
                    f"{patient_id} is assigned to "
                    f"{patient['assigned_doctor']} "
                    f"in {patient['department']}."

            }


        # General admin summary

        return {

            "reply":

                f"Patient "
                f"{patient['first_name']} "
                f"{patient['last_name']} "
                f"({patient_id}). "

                f"Phone: "
                f"{patient['phone']}. "

                f"Insurance: "
                f"{patient['insurance_provider']}. "

                f"Assigned doctor: "
                f"{patient['assigned_doctor']}. "

                f"Department: "
                f"{patient['department']}. "

                f"Next appointment: "
                f"{patient['next_appointment']}. "

                f"Status: "
                f"{patient['appointment_status']}."

        }


    raise HTTPException(
        status_code=403,
        detail="Unsupported role"
    )


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