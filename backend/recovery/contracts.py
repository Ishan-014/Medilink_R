from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, JsonValue, model_validator
from backend.api.schemas import Contract


class ReconcileInput(Contract):
    expected_version: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=1000)


class LookupRequest(Contract):
    reconciliation_id: UUID
    workflow_id: UUID
    tool_call_id: UUID
    approval_id: UUID
    operation: Literal["ehr.create_patient", "insurance.submit_claim"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    arguments_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class LookupResult(LookupRequest):
    outcome: Literal["succeeded", "not_applied", "pending"]
    external_reference: str | None = Field(
        default=None, pattern=r"^external-receipt:[A-Za-z0-9._:-]{1,160}$"
    )
    result: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def coherent_receipt(self):
        if (self.external_reference is not None) != (self.outcome != "pending"):
            raise ValueError(
                "Terminal outcomes require an authoritative receipt reference"
            )
        if (self.result is not None) != (self.outcome == "succeeded"):
            raise ValueError("Only successful writes may carry domain result evidence")
        return self


class ReconciliationRecord(Contract):
    id: UUID
    workflow_id: UUID
    tool_call_id: UUID
    actor_user_id: str
    reason: str
    previous_status: Literal["pending", "uncertain"]
    original_error_code: str | None
    status: Literal["pending", "succeeded", "not_applied", "unresolved", "failed"]
    external_reference: str | None
    error_code: str | None
    started_at: datetime
    finished_at: datetime | None
