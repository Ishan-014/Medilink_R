"""Typed domain evidence captured only from confirmed backend tool executions."""

import hashlib
from datetime import date
from decimal import Decimal

from psycopg2.extras import Json
from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field, StrictBool, ValidationError, model_validator

from backend.integrations.client import checked_json
from backend.integrations.errors import IntegrationError
from backend.services.business_service import CLAIM_READS, add_step, set_phase
from backend.services.workflow_service import lock_key


class Source(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class PatientSource(Source):
    patient_id: str = Field(min_length=1, max_length=128, validation_alias=AliasChoices("patient_id", "id"))
    full_name: str = Field(min_length=1, max_length=200, validation_alias=AliasChoices("full_name", "name"))
    date_of_birth: date
    email: EmailStr

    @model_validator(mode="before")
    @classmethod
    def consistent_identifiers(cls, value):
        if isinstance(value, dict) and "id" in value and "patient_id" in value and value["id"] != value["patient_id"]:
            raise ValueError("Contradictory patient identifiers")
        return value


class ProcedureCharge(Source):
    procedure_code: str
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2, allow_inf_nan=False)
    currency: str


class AmountValidation(ProcedureCharge):
    patient_id: str
    valid: StrictBool


class PolicySource(Source):
    patient_id: str
    policy_id: str
    active: StrictBool
    currency: str


class CoverageSource(ProcedureCharge):
    patient_id: str
    policy_id: str
    covered: StrictBool


class SubmissionSource(CoverageSource):
    claim_id: str = Field(min_length=1, max_length=128)
    status: str


def unwrap(result):
    return result["result"] if isinstance(result, dict) and set(result) == {"result"} else result


def invalid():
    return IntegrationError("MCP_OUTPUT_INVALID", dispatched=True)


def remember(cursor, case, call, operation, facts):
    cursor.execute("""INSERT INTO workflow_case_evidence (workflow_id,operation,tool_call_id,facts)
        VALUES (%s,%s,%s,%s) ON CONFLICT (workflow_id,operation)
        DO UPDATE SET tool_call_id=EXCLUDED.tool_call_id,facts=EXCLUDED.facts""",
        (str(case["workflow_id"]), operation, str(call["id"]), Json(facts)))


def confirm_patient(cursor, case, call, patient):
    data = case["intake"]
    if (patient.email.lower() != data["contact_email"].lower()
            or patient.date_of_birth.isoformat() != data["date_of_birth"]
            or " ".join(patient.full_name.casefold().split()) != " ".join(data["full_name"].casefold().split())):
        set_phase(cursor, str(case["workflow_id"]), "waiting_data", ["patient_record_review"])
        return
    lock_key(cursor, f"patient-link:{patient.patient_id}")
    cursor.execute("SELECT 1 FROM patient_profiles WHERE external_patient_id=%s AND id<>%s", (patient.patient_id, str(case["entity_id"])))
    if cursor.fetchone():
        set_phase(cursor, str(case["workflow_id"]), "waiting_data", ["patient_record_review"])
        return
    cursor.execute("""UPDATE patient_profiles SET external_patient_id=%s, confirmation_call_id=%s,
        status='active', updated_at=clock_timestamp() WHERE id=%s AND status='draft'""",
        (patient.patient_id, str(call["id"]), str(case["entity_id"])))
    if cursor.rowcount != 1:
        raise invalid()
    set_phase(cursor, str(case["workflow_id"]), "confirmed")


def capture(cursor, call, arguments, result):
    cursor.execute("SELECT * FROM workflow_cases WHERE workflow_id=%s", (str(call["workflow_id"]),))
    case = cursor.fetchone()
    if not case:
        return
    if call["status"] != "succeeded" or not call["result_reference"]:
        raise invalid()
    if arguments is None or hashlib.sha256(checked_json(arguments, 65536, "MCP_INPUT_INVALID")).hexdigest() != call["request_fingerprint"]:
        raise invalid()
    operation = f"{call['server']}.{call['tool']}"
    value = unwrap(result)
    workflow_id = str(case["workflow_id"])
    data = case["intake"]
    if case["kind"] == "onboarding":
        if operation == "ehr.search_patients":
            if not isinstance(value, list) or len(value) >= 100:
                set_phase(cursor, workflow_id, "waiting_data", ["ehr_search_evidence"])
                return
            try:
                patients = [PatientSource.model_validate(row) for row in value]
            except ValidationError:
                set_phase(cursor, workflow_id, "waiting_data", ["ehr_search_evidence"])
                return
            matches = [p for p in patients if p.email.lower() == data["contact_email"].lower()]
            remember(cursor, case, call, operation, {"matching_records": len(matches)})
            if len(matches) == 1:
                confirm_patient(cursor, case, call, matches[0])
            elif len(matches) > 1:
                set_phase(cursor, workflow_id, "waiting_data", ["duplicate_patient_review"])
            else:
                add_step(cursor, workflow_id, "ehr.create_patient", 1)
                set_phase(cursor, workflow_id, "awaiting_approval")
        elif operation == "ehr.create_patient":
            try:
                patient = PatientSource.model_validate(value)
            except ValidationError as exc:
                raise invalid() from exc
            remember(cursor, case, call, operation, patient.model_dump(mode="json"))
            confirm_patient(cursor, case, call, patient)
        else:
            raise invalid()
        return
    models = {"ehr.get_patient": PatientSource, "billing.get_procedure_charge": ProcedureCharge,
              "billing.validate_claim_amount": AmountValidation, "insurance.get_policy": PolicySource,
              "insurance.validate_coverage": CoverageSource, "insurance.submit_claim": SubmissionSource}
    try:
        facts = models[operation].model_validate(value).model_dump(mode="json")
    except (ValidationError, KeyError):
        set_phase(cursor, workflow_id, "waiting_data", [operation.replace(".", "_") + "_evidence"])
        return
    # A schema-valid answer about a different patient/policy/procedure is rejected.
    for field, expected in (("patient_id", data["external_patient_id"]), ("policy_id", data["policy_id"]),
                            ("procedure_code", data["procedure_code"]), ("currency", data["currency"])):
        if field in facts and facts[field] != expected:
            raise invalid()
    if "amount" in facts and operation != "billing.get_procedure_charge" and Decimal(facts["amount"]) != Decimal(data["amount"]):
        raise invalid()
    if operation == "ehr.get_patient" and facts["email"].lower() != data["patient_email"].lower():
        raise invalid()
    remember(cursor, case, call, operation, facts)
    for flag, missing in (("valid", "claim_amount_validation"), ("active", "active_policy"), ("covered", "coverage_evidence")):
        if facts.get(flag) is False:
            set_phase(cursor, workflow_id, "waiting_data", [missing])
            return
    if operation == "insurance.submit_claim":
        if facts["status"] != "submitted" or not facts["covered"]:
            raise invalid()
        cursor.execute("""UPDATE claims SET external_claim_id=%s, submission_call_id=%s, status='submitted'
            WHERE id=%s AND status='prepared'""", (facts["claim_id"], str(call["id"]), str(case["entity_id"])))
        if cursor.rowcount != 1:
            raise invalid()
        set_phase(cursor, workflow_id, "confirmed")
    else:
        cursor.execute("SELECT operation FROM workflow_case_evidence WHERE workflow_id=%s", (workflow_id,))
        if set(CLAIM_READS) <= {row["operation"] for row in cursor.fetchall()}:
            from backend.services.graph_service import evidence_ready
            if not data.get("use_graph") or evidence_ready(cursor, workflow_id):
                set_phase(cursor, workflow_id, "awaiting_approval")
