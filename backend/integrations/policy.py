"""Backend-owned allowlist. Server annotations never grant execution permission."""

READ_TOOLS = {
    "ehr": {"search_patients", "get_patient", "get_record", "get_patient_conditions",
            "get_patient_medications", "get_patient_allergies", "get_patient_summary",
            "get_patient_appointment_info", "get_patient_lab_summary"},
    "lab": {"get_results", "get_test_status", "list_tests"},
    "scheduling": {"find_slots"},
    "insurance": {"get_policy", "validate_coverage", "get_claim_status"},
    "billing": {"get_bill", "get_procedure_charge", "validate_claim_amount"},
}
WRITE_TOOLS = {
    "ehr": {"create_patient", "update_record", "update_patient_conditions",
            "update_patient_medications", "update_patient_allergies"},
    "scheduling": {"book_slot", "cancel_slot"}, "insurance": {"submit_claim"},
}


def split_operation(operation: str):
    parts = operation.split(".")
    if len(parts) != 2:
        return None
    server, tool = parts
    return (server, tool) if tool in READ_TOOLS.get(server, set()) | WRITE_TOOLS.get(server, set()) else None
