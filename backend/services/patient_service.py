import os
from typing import Any, Dict, List, Optional

import psycopg2
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


def get_db_connection():
    """Create a PostgreSQL connection using the project's existing env config."""
    return psycopg2.connect(
        dbname=os.getenv("MEDILINK_DB_NAME", "medilink"),
        host=os.getenv("MEDILINK_DB_HOST", "localhost"),
        port=os.getenv("MEDILINK_DB_PORT", "5432"),
        user=os.getenv("MEDILINK_DB_USER", "postgres"),
        password=os.getenv("MEDILINK_DB_PASSWORD"),
    )


def _row_to_dict(cursor, row):
    if row is None:
        return None
    columns = [description[0] for description in cursor.description]
    return dict(zip(columns, row))


def search_patients(query: str) -> List[Dict[str, Any]]:
    """Search the patients table by patient id, name, phone, email, or conditions."""
    if not query or not str(query).strip():
        return []

    search = f"%{query.strip()}%"
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM patients
                WHERE id ILIKE %s
                   OR name ILIKE %s
                   OR phone ILIKE %s
                   OR email ILIKE %s
                   OR conditions ILIKE %s
                ORDER BY id
                LIMIT 100
                """,
                (search, search, search, search, search),
            )
            rows = cursor.fetchall()
            return [_row_to_dict(cursor, row) for row in rows]


def get_patient(patient_id: str) -> Optional[Dict[str, Any]]:
    """Return the complete patient record for a known patient ID."""
    if patient_id is None:
        return None

    value = str(patient_id).strip()
    if not value:
        return None

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM patients
                WHERE id = %s
                LIMIT 1
                """,
                (value,),
            )
            row = cursor.fetchone()
            return _row_to_dict(cursor, row)


def get_patient_conditions(patient_id: str) -> Optional[Dict[str, Any]]:
    """Return a patient's id, name, and conditions."""
    patient = get_patient(patient_id)
    if not patient:
        return None

    return {
        "patient_id": patient.get("id"),
        "name": patient.get("name"),
        "conditions": patient.get("conditions"),
    }


def get_patient_medications(patient_id: str) -> Optional[Dict[str, Any]]:
    """Return a patient's id, name, and medications."""
    patient = get_patient(patient_id)
    if not patient:
        return None

    return {
        "patient_id": patient.get("id"),
        "name": patient.get("name"),
        "medications": patient.get("medications"),
    }


def get_patient_allergies(patient_id: str) -> Optional[Dict[str, Any]]:
    """Return a patient's id, name, and allergies."""
    patient = get_patient(patient_id)
    if not patient:
        return None

    return {
        "patient_id": patient.get("id"),
        "name": patient.get("name"),
        "allergies": patient.get("allergies"),
    }


def get_patient_summary(patient_id: str) -> Optional[Dict[str, Any]]:
    """Return a consolidated patient summary for a given patient ID."""
    patient = get_patient(patient_id)
    if not patient:
        return None

    return {
        "id": patient.get("id"),
        "name": patient.get("name"),
        "date_of_birth": patient.get("date_of_birth"),
        "gender": patient.get("gender"),
        "blood_group": patient.get("blood_group"),
        "phone": patient.get("phone"),
        "email": patient.get("email"),
        "allergies": patient.get("allergies"),
        "conditions": patient.get("conditions"),
        "medications": patient.get("medications"),
    }


def get_patient_appointment_info(patient_id: str) -> Optional[Dict[str, Any]]:
    """Return appointment and follow-up scheduling data for a patient."""
    patient = get_patient(patient_id)
    if not patient:
        return None

    return {
        "patient_id": patient.get("id"),
        "name": patient.get("name"),
        "assigned_doctor": patient.get("assigned_doctor"),
        "department": patient.get("department"),
        "last_visit": patient.get("last_visit"),
        "next_appointment": patient.get("next_appointment"),
        "appointment_status": patient.get("appointment_status"),
    }


def get_patient_lab_summary(patient_id: str) -> Optional[Dict[str, Any]]:
    """Return the lab summary and diagnostic context for a patient."""
    patient = get_patient(patient_id)
    if not patient:
        return None

    return {
        "patient_id": patient.get("id"),
        "name": patient.get("name"),
        "lab_summary": patient.get("lab_summary"),
        "diagnosis_summary": patient.get("diagnosis_summary"),
        "last_visit": patient.get("last_visit"),
        "conditions": patient.get("conditions"),
    }


def generate_clinical_brief(patient_id: str, focus: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Generate a clinically-safe brief from structured EHR fields using the configured OpenAI model."""
    patient = get_patient(patient_id)
    if not patient:
        return None

    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", "gpt-5-mini")

    patient_context = {
        "patient_id": patient.get("id"),
        "name": patient.get("name"),
        "date_of_birth": patient.get("date_of_birth"),
        "gender": patient.get("gender"),
        "blood_group": patient.get("blood_group"),
        "conditions": patient.get("conditions"),
        "medications": patient.get("medications"),
        "allergies": patient.get("allergies"),
        "lab_summary": patient.get("lab_summary"),
        "diagnosis_summary": patient.get("diagnosis_summary"),
        "next_appointment": patient.get("next_appointment"),
        "appointment_status": patient.get("appointment_status"),
    }

    if not api_key:
        return {
            "patient_id": patient.get("id"),
            "name": patient.get("name"),
            "brief": (
                "Clinical brief unavailable because the OpenAI key is not configured. "
                "Use the structured patient record fields for safe manual review."
            ),
            "source": "EHR fallback",
            "focus": focus,
            "context": patient_context,
        }

    client = OpenAI(api_key=api_key)
    focus_text = focus.strip() if focus and str(focus).strip() else "general overview"

    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    "You are a clinical assistant for a doctor. Use only the supplied EHR data. "
                    "Do not invent findings. Do not provide autonomous diagnosis. "
                    "Keep the response concise, structured, and clinically safe."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Create a short doctor-facing clinical brief for patient {patient.get('id')} "
                    f"({patient.get('name')}) focusing on {focus_text}. "
                    f"Use only this EHR data: {patient_context}"
                ),
            },
        ],
        temperature=0.2,
    )

    answer = getattr(response, "output_text", None) or str(response)
    return {
        "patient_id": patient.get("id"),
        "name": patient.get("name"),
        "brief": answer,
        "source": "OpenAI clinical summary",
        "focus": focus_text,
        "context": patient_context,
    }


def _update_patient_field(patient_id: str, field_name: str, value: Any) -> Optional[Dict[str, Any]]:
    """Safely update a single patient field in PostgreSQL and return a consistent doctor-facing payload."""
    if patient_id is None:
        return None

    cleaned_patient_id = str(patient_id).strip()
    if not cleaned_patient_id:
        return None

    if value is None:
        value = ""

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE patients
                SET {field_name} = %s
                WHERE id = %s
                RETURNING *
                """,
                (value, cleaned_patient_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            updated = _row_to_dict(cursor, row)
            conn.commit()

            return {
                "patient_id": updated.get("id"),
                "name": updated.get("name"),
                field_name: updated.get(field_name),
            }


def update_patient_conditions(patient_id: str, conditions: str) -> Optional[Dict[str, Any]]:
    """Update the patient's conditions field and return the updated patient record."""
    return _update_patient_field(patient_id, "conditions", conditions)


def update_patient_medications(patient_id: str, medications: str) -> Optional[Dict[str, Any]]:
    """Update the patient's medications field and return the updated patient record."""
    return _update_patient_field(patient_id, "medications", medications)


def update_patient_allergies(patient_id: str, allergies: str) -> Optional[Dict[str, Any]]:
    """Update the patient's allergies field and return the updated patient record."""
    return _update_patient_field(patient_id, "allergies", allergies)


