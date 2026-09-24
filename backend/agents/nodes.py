import json
import os
from typing import Any, Dict, List

from dotenv import load_dotenv
from openai import OpenAI

from backend.mcp.patient_tools import (
    generate_clinical_brief, get_patient, get_patient_allergies,
    get_patient_appointment_info, get_patient_conditions,
    get_patient_lab_summary, get_patient_medications,
    get_patient_summary, search_patients,
    update_patient_allergies, update_patient_conditions,
    update_patient_medications,
)

load_dotenv()
MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

SYSTEM_PROMPT = """You are MediLink AI, a conversational healthcare assistant for authenticated MediLink staff.
Have a real conversation. Answer greetings, small talk and general questions naturally without patient tools.
Use patient tools whenever live EHR data is needed. Decide from the full conversation, not keywords.
Resolve pronouns and references such as he, she, him, her, that patient and the same patient from prior turns.
If a patient is known by name but ID is unknown, search_patients first. If multiple patients match, ask for clarification.
Never invent patient data. PostgreSQL tool results are the source of truth. Say when data is unavailable.
Do not autonomously diagnose. Keep clinical answers concise and doctor-facing.
Only use update_patient_* when the user explicitly asks to change a record and the patient/value are unambiguous.
You may call multiple tools in one turn. Search first, then retrieve the requested field. Answer naturally after tool results.
Do not expose internal tool names unless asked."""

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

def _fn(name, description, props, required):
    return {
        "type": "function", "name": name, "description": description,
        "parameters": {"type": "object", "properties": props,
                       "required": required, "additionalProperties": False},
    }

TOOL_DEFINITIONS = [
    _fn("search_patients", "Search the MediLink patient registry.",
        {"query": {"type": "string"}}, ["query"]),
    _fn("get_patient", "Retrieve the complete EHR record for a known patient ID.",
        {"patient_id": {"type": "string"}}, ["patient_id"]),
    _fn("get_patient_conditions", "Return recorded patient conditions.",
        {"patient_id": {"type": "string"}}, ["patient_id"]),
    _fn("get_patient_medications", "Return recorded patient medications.",
        {"patient_id": {"type": "string"}}, ["patient_id"]),
    _fn("get_patient_allergies", "Return recorded patient allergies.",
        {"patient_id": {"type": "string"}}, ["patient_id"]),
    _fn("get_patient_summary", "Return the consolidated patient summary from PostgreSQL.",
        {"patient_id": {"type": "string"}}, ["patient_id"]),
    _fn("get_patient_appointment_info", "Return patient appointment and follow-up information.",
        {"patient_id": {"type": "string"}}, ["patient_id"]),
    _fn("get_patient_lab_summary", "Return available patient lab and diagnostic summary.",
        {"patient_id": {"type": "string"}}, ["patient_id"]),
    _fn("generate_clinical_brief", "Generate a concise doctor-facing clinical brief.",
        {"patient_id": {"type": "string"}, "focus": {"type": "string"}}, ["patient_id"]),
    _fn("update_patient_conditions", "Explicitly record a change to patient conditions.",
        {"patient_id": {"type": "string"}, "conditions": {"type": "string"}}, ["patient_id","conditions"]),
    _fn("update_patient_medications", "Explicitly record a change to patient medications.",
        {"patient_id": {"type": "string"}, "medications": {"type": "string"}}, ["patient_id","medications"]),
    _fn("update_patient_allergies", "Explicitly record a change to patient allergies.",
        {"patient_id": {"type": "string"}, "allergies": {"type": "string"}}, ["patient_id","allergies"]),
]

def _client():
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    return OpenAI(api_key=key)

def _args(item):
    raw = getattr(item, "arguments", "{}") or "{}"
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}

def _history(history):
    result = []
    for m in history or []:
        role = m.get("role")
        if role not in ("user", "assistant"):
            role = "user" if m.get("sender") == "user" else "assistant"
        content = str(m.get("content") or m.get("text") or "").strip()
        if content:
            result.append({"role": role, "content": content})
    return result

def run_agent(user_message: str, history: List[Dict[str, Any]] | None = None) -> str:
    message = (user_message or "").strip()
    if not message:
        return "Please enter a message."

    client = _client()
    conversation = _history(history)
    conversation.append({"role": "user", "content": message})

    for _ in range(5):
        response = client.responses.create(
            model=MODEL,
            instructions=SYSTEM_PROMPT,
            input=conversation,
            tools=TOOL_DEFINITIONS,
        )
        output = getattr(response, "output", []) or []
        calls = [x for x in output if getattr(x, "type", None) == "function_call"]

        if not calls:
            answer = getattr(response, "output_text", None)
            return answer.strip() if answer and answer.strip() else "I couldn't generate a response."

        for item in output:
            if getattr(item, "type", None) == "function_call":
                conversation.append({
                    "type": "function_call",
                    "call_id": getattr(item, "call_id", ""),
                    "name": getattr(item, "name", ""),
                    "arguments": getattr(item, "arguments", "{}"),
                })

        for call in calls:
            name = getattr(call, "name", "")
            fn = TOOL_MAP.get(name)
            try:
                result = fn(**_args(call)) if fn else {"error": f"Unknown tool: {name}"}
            except Exception as exc:
                result = {"error": str(exc)}
            conversation.append({
                "type": "function_call_output",
                "call_id": getattr(call, "call_id", ""),
                "output": json.dumps(result, default=str),
            })

    return "I could not complete that request within the allowed tool steps."
