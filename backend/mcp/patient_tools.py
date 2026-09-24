from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from backend.services.patient_service import (
    generate_clinical_brief as generate_clinical_brief_service,
    get_patient as get_patient_service,
    get_patient_allergies as get_patient_allergies_service,
    get_patient_appointment_info as get_patient_appointment_info_service,
    get_patient_conditions as get_patient_conditions_service,
    get_patient_lab_summary as get_patient_lab_summary_service,
    get_patient_medications as get_patient_medications_service,
    get_patient_summary as get_patient_summary_service,
    search_patients as search_patients_service,
    update_patient_allergies as update_patient_allergies_service,
    update_patient_conditions as update_patient_conditions_service,
    update_patient_medications as update_patient_medications_service,
)

mcp = FastMCP("MediLink Patient Tools")


@mcp.tool()
def search_patients(query: str) -> List[Dict[str, Any]]:
    """Search the MediLink patient registry by patient ID, name, phone, email, or condition."""
    return search_patients_service(query)


@mcp.tool()
def get_patient(patient_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve the complete available patient record for a known patient ID."""
    return get_patient_service(patient_id)


@mcp.tool()
def get_patient_conditions(patient_id: str) -> Optional[Dict[str, Any]]:
    """Return the patient's ID, name, and recorded conditions for the specified patient."""
    return get_patient_conditions_service(patient_id)


@mcp.tool()
def get_patient_medications(patient_id: str) -> Optional[Dict[str, Any]]:
    """Return the patient's ID, name, and recorded medications for the specified patient."""
    return get_patient_medications_service(patient_id)


@mcp.tool()
def get_patient_allergies(patient_id: str) -> Optional[Dict[str, Any]]:
    """Return the patient's ID, name, and recorded allergies for the specified patient."""
    return get_patient_allergies_service(patient_id)


@mcp.tool()
def get_patient_summary(patient_id: str) -> Optional[Dict[str, Any]]:
    """Return the consolidated patient summary for the specified patient ID from PostgreSQL."""
    return get_patient_summary_service(patient_id)


@mcp.tool()
def get_patient_appointment_info(patient_id: str) -> Optional[Dict[str, Any]]:
    """Return scheduling information such as next appointment, follow-up date, and assigned doctor."""
    return get_patient_appointment_info_service(patient_id)


@mcp.tool()
def get_patient_lab_summary(patient_id: str) -> Optional[Dict[str, Any]]:
    """Return the patient's EHR lab summary and related clinical context."""
    return get_patient_lab_summary_service(patient_id)


@mcp.tool()
def generate_clinical_brief(patient_id: str, focus: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Create a clinically safe, doctor-facing brief from the patient record using the configured LLM."""
    return generate_clinical_brief_service(patient_id, focus=focus)


@mcp.tool()
def update_patient_conditions(patient_id: str, conditions: str) -> Optional[Dict[str, Any]]:
    """Update a patient’s conditions field in PostgreSQL and return the updated record."""
    return update_patient_conditions_service(patient_id, conditions)


@mcp.tool()
def update_patient_medications(patient_id: str, medications: str) -> Optional[Dict[str, Any]]:
    """Update a patient’s medications field in PostgreSQL and return the updated record."""
    return update_patient_medications_service(patient_id, medications)


@mcp.tool()
def update_patient_allergies(patient_id: str, allergies: str) -> Optional[Dict[str, Any]]:
    """Update a patient’s allergies field in PostgreSQL and return the updated record."""
    return update_patient_allergies_service(patient_id, allergies)


search_patients_tool = search_patients
get_patient_tool = get_patient
get_patient_conditions_tool = get_patient_conditions
get_patient_medications_tool = get_patient_medications
get_patient_allergies_tool = get_patient_allergies
get_patient_summary_tool = get_patient_summary
get_patient_appointment_info_tool = get_patient_appointment_info
get_patient_lab_summary_tool = get_patient_lab_summary
generate_clinical_brief_tool = generate_clinical_brief
update_patient_conditions_tool = update_patient_conditions
update_patient_medications_tool = update_patient_medications
update_patient_allergies_tool = update_patient_allergies

__all__ = [
    "mcp",
    "search_patients",
    "search_patients_tool",
    "get_patient",
    "get_patient_tool",
    "get_patient_conditions",
    "get_patient_conditions_tool",
    "get_patient_medications",
    "get_patient_medications_tool",
    "get_patient_allergies",
    "get_patient_allergies_tool",
    "get_patient_summary",
    "get_patient_summary_tool",
    "get_patient_appointment_info",
    "get_patient_appointment_info_tool",
    "get_patient_lab_summary",
    "get_patient_lab_summary_tool",
    "generate_clinical_brief",
    "generate_clinical_brief_tool",
    "update_patient_conditions",
    "update_patient_conditions_tool",
    "update_patient_medications",
    "update_patient_medications_tool",
    "update_patient_allergies",
    "update_patient_allergies_tool",
]
