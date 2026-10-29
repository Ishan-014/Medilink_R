from backend.api.schemas import Role
from backend.graph.contracts import GRAPH_OPERATIONS
from backend.integrations.policy import READ_TOOLS, WRITE_TOOLS
from backend.integrations.errors import IntegrationError

POLICY_VERSION = "medilink-mvp-1"
ENABLED_WRITES = {"ehr.create_patient": "onboarding", "insurance.submit_claim": "insurance_claim"}


def classify(operation):
    if operation in {f"graph.{op}" for op in GRAPH_OPERATIONS}:
        return "low"
    parts = operation.split(".")
    if len(parts) != 2:
        raise IntegrationError("MCP_WRITE_BLOCKED")
    server, tool = parts
    if tool in READ_TOOLS.get(server, set()):
        return "low"
    if tool in WRITE_TOOLS.get(server, set()):
        return "medium" if operation in {"ehr.create_patient", "scheduling.book_slot", "scheduling.cancel_slot"} else "high"
    raise IntegrationError("MCP_WRITE_BLOCKED")


def reviewers(operation):
    return {Role.ADMIN} if operation == "insurance.submit_claim" else {Role.DOCTOR, Role.ADMIN}


def needs_review(operation, settings):
    risk = classify(operation)
    return risk == "high" or (risk == "medium" and settings.medium_risk_review == "required")
