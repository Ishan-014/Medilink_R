import json
import os
import re
from typing import Any, Dict

from dotenv import load_dotenv
from openai import OpenAI

from backend.mcp.patient_tools import (
    generate_clinical_brief,
    get_patient,
    get_patient_allergies,
    get_patient_appointment_info,
    get_patient_conditions,
    get_patient_lab_summary,
    get_patient_medications,
    get_patient_summary,
    search_patients,
    update_patient_allergies,
    update_patient_conditions,
    update_patient_medications,
)

load_dotenv()

SYSTEM_PROMPT = """
You are MediLink AI Assistant, a clinical-information assistant for doctors.

Rules:
- Only use MCP tools for patient-specific or clinically relevant requests.
- For greetings, small talk, general conversation, or non-patient requests, respond normally without tools.
- Never invent patient information.
- Never fabricate diagnoses, medications, allergies, or medical history.
- Base patient-specific answers only on PostgreSQL data returned through MCP tools.
- If a requested patient cannot be found, say so.
- If information is not available in the current EHR data, say so.
- Provide concise, structured responses for doctors.
- Do not provide autonomous medical diagnosis.
- Only use the write-capable patient update tools when the user explicitly asks to update a medical record field.
- Treat PostgreSQL data returned through MCP as the source of truth for patient records.

When the user asks for a patient by name, first use search_patients to find the patient if the ID is not already known.
When the user asks for conditions, medications, allergies, summary, or updates, use the corresponding MCP tool after identifying the patient.
"""

MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

TOOL_MAP = {
    "search_patients": search_patients,
    "get_patient": get_patient,
    "get_patient_conditions": get_patient_conditions,
    "get_patient_medications": get_patient_medications,
    "get_patient_allergies": get_patient_allergies,
    "get_patient_summary": get_patient_summary,
    "get_patient_appointment_info": get_patient_appointment_info,
    "get_patient_lab_summary": get_patient_lab_summary,
    "generate_clinical_brief": generate_clinical_brief,
    "update_patient_conditions": update_patient_conditions,
    "update_patient_medications": update_patient_medications,
    "update_patient_allergies": update_patient_allergies,
}


def _state_get(state: Any, key: str, default: Any = None) -> Any:
    if isinstance(state, dict):
        return state.get(key, default)
    return getattr(state, key, default)


def _is_general_chat(message: str) -> bool:
    lowered = message.lower().strip()
    if not lowered:
        return True

    greetings = [
        "hello", "hi", "hey", "good morning", "good afternoon", "good evening",
        "greetings", "yo", "sup", "how are you", "how's it going",
        "thanks", "thank you", "okay", "ok", "bye", "goodbye"
    ]
    if lowered in greetings:
        return True

    patient_intent = re.search(
        r"\b(?:find|search|lookup|look up|show me|give me|tell me|who is|what is)\b.*\b[A-Z][a-z]+\b",
        message,
    ) or re.search(
        r"\b(?:info|information|details|profile|record)\s+(?:on|for)\s+[A-Z][a-z]+\b",
        message,
    )
    if patient_intent:
        return False

    if re.search(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", message):
        if any(token in lowered for token in ["find", "search", "lookup", "look up", "show me", "information", "info", "details", "profile", "record", "patient", "records"]):
            return False

    greeting_prefix = False
    for greeting in greetings:
        if lowered.startswith(greeting):
            greeting_prefix = True
            break

    if greeting_prefix and patient_intent:
        return False

    if len(message.split()) <= 5 and not any(word in lowered for word in [
        "patient", "record", "records", "medication", "medications", "allergy", "allergies",
        "condition", "conditions", "diagnosis", "diagnoses", "summary", "appointment", "lab",
        "clinical", "brief", "doctor", "info", "information", "details", "profile"
    ]):
        return True

    return False


def _looks_like_patient_request(message: str) -> bool:
    lowered = message.lower().strip()
    patient_markers = [
        "patient", "patients", "record", "records", "medical record", "ehr",
        "medication", "medications", "medicine", "allergy", "allergies",
        "condition", "conditions", "diagnosis", "diagnoses", "lab", "labs",
        "appointment", "schedule", "follow-up", "clinical brief", "summary",
        "brief", "find patient", "search patient", "look up patient", "patient info", "patient details",
        "information on", "info on", "details on", "profile on", "record on"
    ]
    if any(marker in lowered for marker in patient_markers):
        return True

    name_patterns = [
        r"\b(?:find|search|lookup|look up|show me|give me|tell me|who is|what is)\b.*\b[A-Z][a-z]+\b",
        r"\b(?:info|information|details|profile|record)\s+(?:on|for)\s+[A-Z][a-z]+\b",
        r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b",
    ]
    if any(re.search(pattern, message) for pattern in name_patterns):
        if any(token in lowered for token in ["find", "search", "lookup", "look up", "show me", "give me", "tell me", "information", "info", "details", "profile", "record", "patient", "records"]):
            return True

    if re.search(r"\b[A-Z][a-z]+\b", message) and any(token in lowered for token in ["find", "search", "lookup", "look up", "show me", "information", "info", "details", "profile", "record", "patient"]):
        return True

    return False


def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


def _fallback_tool_selection(user_message: str) -> Dict[str, Any]:
    lowered = user_message.lower()
    if not lowered.strip():
        return {"tool_name": None, "tool_arguments": {}}

    if re.search(r"\b(?:find|search)\b.*\b[a-z]+\b", lowered):
        name = re.findall(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+|[A-Za-z]+\s+[A-Za-z]+", user_message)
        if name:
            return {"tool_name": "search_patients", "tool_arguments": {"query": name[0]}}

    match = re.search(r"\bPAT-\d+\b|\b[A-Z]{2,}-\d+\b", user_message, flags=re.IGNORECASE)
    patient_id = match.group(0).upper() if match else None

    if any(word in lowered for word in ["update condition", "set conditions", "change conditions", "conditions are", "conditions ="]):
        return {"tool_name": "update_patient_conditions", "tool_arguments": {"patient_id": patient_id or "PAT-001", "conditions": user_message}}
    if any(word in lowered for word in ["update medication", "set medications", "change medications", "medications are", "medications ="]):
        return {"tool_name": "update_patient_medications", "tool_arguments": {"patient_id": patient_id or "PAT-001", "medications": user_message}}
    if any(word in lowered for word in ["update allergy", "set allergies", "change allergies", "allergies are", "allergies ="]):
        return {"tool_name": "update_patient_allergies", "tool_arguments": {"patient_id": patient_id or "PAT-001", "allergies": user_message}}
    if any(word in lowered for word in ["brief", "clinical brief", "case review", "summary report", "doctor brief"]):
        return {"tool_name": "generate_clinical_brief", "tool_arguments": {"patient_id": patient_id or "PAT-001", "focus": user_message}}
    if any(word in lowered for word in ["lab", "labs", "lab summary", "lab results", "bloodwork"]):
        return {"tool_name": "get_patient_lab_summary", "tool_arguments": {"patient_id": patient_id or "PAT-001"}}
    if any(word in lowered for word in ["appointment", "schedule", "follow-up", "next visit", "next appointment", "visit timing"]):
        return {"tool_name": "get_patient_appointment_info", "tool_arguments": {"patient_id": patient_id or "PAT-001"}}
    if any(word in lowered for word in ["summary", "complete summary", "full summary"]):
        return {"tool_name": "get_patient_summary", "tool_arguments": {"patient_id": patient_id or "PAT-001"}}
    if any(word in lowered for word in ["allergy", "allergies"]):
        return {"tool_name": "get_patient_allergies", "tool_arguments": {"patient_id": patient_id or "PAT-001"}}
    if any(word in lowered for word in ["medication", "medicines", "medicine"]):
        return {"tool_name": "get_patient_medications", "tool_arguments": {"patient_id": patient_id or "PAT-001"}}
    if any(word in lowered for word in ["condition", "conditions", "diagnosis"]):
        return {"tool_name": "get_patient_conditions", "tool_arguments": {"patient_id": patient_id or "PAT-001"}}
    if patient_id:
        return {"tool_name": "get_patient", "tool_arguments": {"patient_id": patient_id}}

    if re.search(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", user_message):
        return {"tool_name": "search_patients", "tool_arguments": {"query": user_message}}

    return {"tool_name": "search_patients", "tool_arguments": {"query": user_message}}


def choose_tool(state: Dict[str, Any]) -> Dict[str, Any]:
    user_message = _state_get(state, "user_message", "")
    if not user_message:
        return {"tool_name": None, "tool_arguments": {}}

    if _is_general_chat(user_message):
        return {"tool_name": None, "tool_arguments": {}}

    if not _looks_like_patient_request(user_message):
        return {"tool_name": None, "tool_arguments": {}}

    client = get_openai_client()
    if client is None:
        return _fallback_tool_selection(user_message)

    try:
        response = client.responses.create(
            model=MODEL,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            tools=[
                {"type": "function", "name": "search_patients", "description": "Search the MediLink patient registry by patient ID, name, phone, email, or condition.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
                {"type": "function", "name": "get_patient", "description": "Retrieve the complete available patient record for a known patient ID.", "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}}, "required": ["patient_id"]}},
                {"type": "function", "name": "get_patient_conditions", "description": "Return a patient's ID, name, and recorded conditions.", "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}}, "required": ["patient_id"]}},
                {"type": "function", "name": "get_patient_medications", "description": "Return a patient's ID, name, and recorded medications.", "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}}, "required": ["patient_id"]}},
                {"type": "function", "name": "get_patient_allergies", "description": "Return a patient's ID, name, and recorded allergies.", "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}}, "required": ["patient_id"]}},
                {"type": "function", "name": "get_patient_summary", "description": "Return the consolidated patient summary for the specified patient ID from PostgreSQL.", "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}}, "required": ["patient_id"]}},
                {"type": "function", "name": "get_patient_appointment_info", "description": "Return next appointment, follow-up information, and scheduling metadata for a patient.", "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}}, "required": ["patient_id"]}},
                {"type": "function", "name": "get_patient_lab_summary", "description": "Return the EHR lab summary, diagnostic context, and related clinical findings for a patient.", "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}}, "required": ["patient_id"]}},
                {"type": "function", "name": "generate_clinical_brief", "description": "Generate a concise, clinically safe doctor-facing summary from the structured patient record using the configured LLM.", "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}, "focus": {"type": "string"}}, "required": ["patient_id"]}},
                {"type": "function", "name": "update_patient_conditions", "description": "Safely update the patient conditions field in the EHR when the user explicitly asks to record a condition change.", "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}, "conditions": {"type": "string"}}, "required": ["patient_id", "conditions"]}},
                {"type": "function", "name": "update_patient_medications", "description": "Safely update the patient medications field in the EHR when the user explicitly asks to record a medication change.", "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}, "medications": {"type": "string"}}, "required": ["patient_id", "medications"]}},
                {"type": "function", "name": "update_patient_allergies", "description": "Safely update the patient allergies field in the EHR when the user explicitly asks to record an allergy change.", "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}, "allergies": {"type": "string"}}, "required": ["patient_id", "allergies"]}},
            ],
        )

        tool_call = None
        for item in getattr(response, "output", []) or []:
            item_type = getattr(item, "type", None)
            if item_type == "function_call" or getattr(item, "name", None):
                tool_call = item
                break

        if tool_call is None:
            return _fallback_tool_selection(user_message)

        tool_name = getattr(tool_call, "name", None)
        raw_arguments = getattr(tool_call, "arguments", {}) or {}
        if isinstance(raw_arguments, str):
            try:
                tool_arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                tool_arguments = {}
        else:
            tool_arguments = raw_arguments

        return {"tool_name": tool_name, "tool_arguments": tool_arguments}
    except Exception:
        return _fallback_tool_selection(user_message)


def execute_tool(state: Dict[str, Any]) -> Dict[str, Any]:
    tool_name = _state_get(state, "tool_name")
    tool_arguments = _state_get(state, "tool_arguments", {}) or {}
    if not tool_name:
        return {"tool_result": {"error": "No tool selected."}}

    func = TOOL_MAP.get(tool_name)
    if func is None:
        return {"tool_result": {"error": f"Unsupported tool: {tool_name}"}}

    try:
        result = func(**tool_arguments)
        return {"tool_result": result}
    except Exception as exc:
        return {"tool_result": {"error": str(exc)}}


def generate_final_response(state: Dict[str, Any]) -> Dict[str, Any]:
    user_message = _state_get(state, "user_message", "")
    tool_result = _state_get(state, "tool_result")
    tool_name = _state_get(state, "tool_name")

    if not tool_name:
        lowered = user_message.lower().strip()
        if not lowered:
            return {"final_response": "Hello! I’m MediLink AI. Ask me about a patient record, medications, allergies, appointments, or summaries."}
        if _is_general_chat(user_message):
            return {"final_response": "Hello! I’m MediLink AI. Ask me about a patient record, medications, allergies, appointments, or summaries."}
        if _looks_like_patient_request(user_message):
            return {"final_response": "I’m ready to look up the patient record and help with the requested clinical information."}
        return {"final_response": "I’m ready to help with patient information, appointments, medications, allergies, and clinical summaries."}

    if tool_result is None:
        return {"final_response": "I could not retrieve the requested patient information from the MediLink records."}

    context = tool_result.get("error") if isinstance(tool_result, dict) and tool_result.get("error") else str(tool_result)
    client = get_openai_client()
    if client is None:
        if isinstance(tool_result, dict) and tool_result.get("error"):
            return {"final_response": tool_result["error"]}
        return {"final_response": f"I retrieved the following patient data: {context}"}

    try:
        response = client.responses.create(
            model=MODEL,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": f"Here is the factual data from PostgreSQL: {context}"},
            ],
        )
        answer = response.output_text if hasattr(response, "output_text") else str(response)
        return {"final_response": answer}
    except Exception as exc:
        return {"final_response": f"I couldn't generate a response from the retrieved patient data. Error: {exc}"}
