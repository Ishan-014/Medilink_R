from backend.services.patient_service import (
    get_patient,
    get_patient_allergies,
    get_patient_conditions,
    get_patient_medications,
    get_patient_summary,
    update_patient_allergies,
    update_patient_conditions,
    update_patient_medications,
)
from backend.mcp.patient_tools import (
    search_patients_tool,
    get_patient_tool,
    get_patient_conditions_tool,
    get_patient_medications_tool,
    get_patient_allergies_tool,
    get_patient_summary_tool,
    get_patient_appointment_info_tool,
    get_patient_lab_summary_tool,
    update_patient_conditions_tool,
    update_patient_medications_tool,
    update_patient_allergies_tool,
)
from backend.agents.nodes import choose_tool, generate_final_response
from backend.main import normalize_patient_record


def test_normalize_patient_record_returns_frontend_compatible_shape():
    patient = normalize_patient_record({
        "id": "PAT-001",
        "name": "Aarav Sharma",
        "date_of_birth": "2001-05-14",
        "gender": "Male",
        "blood_group": "O+",
        "phone": "9876500001",
        "email": "aarav.sharma@demo.medilink",
        "allergies": "Penicillin",
        "conditions": "Mild asthma",
        "medications": "Salbutamol inhaler",
        "department": "Cardiology",
    })

    assert patient["patient_id"] == "PAT-001"
    assert patient["first_name"] == "Aarav"
    assert patient["last_name"] == "Sharma"
    assert patient["blood_group"] == "O+"
    assert patient["department"] == "Cardiology"


def test_generate_final_response_does_not_use_placeholder_for_patient_queries():
    response = generate_final_response({"user_message": "hello, find me information on Aarav", "tool_name": None})
    assert response["final_response"] != "Hello! I’m MediLink AI. I can help with patient records, medications, allergies, appointments, or summaries when you ask about a specific patient."


def test_agent_routes_greeting_prefixed_patient_queries_to_patient_search():
    prompts = [
        "hello, find me information on Aarav",
        "hi can you look up Aarav Sharma",
        "good morning, show me info on Aarav",
    ]

    for prompt in prompts:
        result = choose_tool({"user_message": prompt})
        assert result["tool_name"] == "search_patients", f"Prompt did not route to patient search: {prompt} -> {result}"


def test_agent_routes_name_lookup_queries_to_patient_search():
    prompts = [
        "find me information on Aarav",
        "find me information on aarav",
        "show me info on Aarav Sharma",
        "information on Aarav",
    ]

    for prompt in prompts:
        result = choose_tool({"user_message": prompt})
        assert result["tool_name"] == "search_patients", f"Prompt did not route to patient search: {prompt} -> {result}"


def test_search_patients_tool_returns_real_db_match():
    expected_patient = get_patient("PAT-001")
    assert expected_patient is not None

    results = search_patients_tool("Aarav Sharma")

    assert isinstance(results, list)
    assert any(item["id"] == expected_patient["id"] for item in results)
    assert any(item["name"] == expected_patient["name"] for item in results)


def test_get_patient_tool_returns_full_record():
    expected = get_patient("PAT-001")
    result = get_patient_tool("PAT-001")

    assert result is not None
    assert result["id"] == expected["id"]
    assert result["name"] == expected["name"]
    assert result["email"] == expected["email"]


def test_patient_condition_medication_allergy_summary_tools():
    expected_conditions = get_patient_conditions("PAT-001")
    expected_medications = get_patient_medications("PAT-001")
    expected_allergies = get_patient_allergies("PAT-001")
    expected_summary = get_patient_summary("PAT-001")

    assert get_patient_conditions_tool("PAT-001") == expected_conditions
    assert get_patient_medications_tool("PAT-001") == expected_medications
    assert get_patient_allergies_tool("PAT-001") == expected_allergies
    assert get_patient_summary_tool("PAT-001") == expected_summary


def test_appointment_and_lab_tools_return_real_patient_data():
    appointment = get_patient_appointment_info_tool("PAT-001")
    lab = get_patient_lab_summary_tool("PAT-001")

    assert appointment is not None
    assert appointment["patient_id"] == "PAT-001"
    assert appointment["name"] == "Aarav Sharma"
    assert lab is not None
    assert lab["patient_id"] == "PAT-001"
    assert lab["name"] == "Aarav Sharma"


def test_write_tools_update_patient_records_and_restore_them():
    patient_id = "PAT-001"
    original_conditions = get_patient(patient_id)["conditions"]
    original_medications = get_patient(patient_id)["medications"]
    original_allergies = get_patient(patient_id)["allergies"]

    try:
        conditions_result = update_patient_conditions_tool(patient_id, "Updated via test condition record")
        medications_result = update_patient_medications_tool(patient_id, "Updated via test medication record")
        allergies_result = update_patient_allergies_tool(patient_id, "Updated via test allergy record")

        assert conditions_result["patient_id"] == patient_id
        assert conditions_result["conditions"] == "Updated via test condition record"
        assert medications_result["medications"] == "Updated via test medication record"
        assert allergies_result["allergies"] == "Updated via test allergy record"

        assert update_patient_conditions(patient_id, original_conditions)["conditions"] == original_conditions
        assert update_patient_medications(patient_id, original_medications)["medications"] == original_medications
        assert update_patient_allergies(patient_id, original_allergies)["allergies"] == original_allergies
    finally:
        update_patient_conditions(patient_id, original_conditions)
        update_patient_medications(patient_id, original_medications)
        update_patient_allergies(patient_id, original_allergies)
