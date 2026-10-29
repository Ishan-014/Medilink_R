from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from backend.api.schemas import Contract, WorkflowRun


class OnboardingData(Contract):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    date_of_birth: date | None = None
    phone: str | None = Field(default=None, pattern=r"^\+?[0-9][0-9 ()-]{5,24}$")
    document_ids: list[UUID] | None = Field(default=None, max_length=20)

    @field_validator("date_of_birth")
    @classmethod
    def no_future_birth(cls, value):
        if value and value > datetime.now(timezone.utc).date():
            raise ValueError("Birth date cannot be in the future")
        return value


class OnboardingStart(OnboardingData):
    patient_user_id: str | None = Field(default=None, min_length=1, max_length=100)
    request: str = Field(default="Start patient onboarding", min_length=1, max_length=10000)


class OnboardingUpdate(OnboardingData):
    expected_version: int = Field(ge=0, strict=True)


class ClaimData(Contract):
    policy_id: str | None = Field(default=None, min_length=1, max_length=100)
    procedure_code: str | None = Field(default=None, min_length=1, max_length=100)
    amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2, allow_inf_nan=False)
    currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")
    document_ids: list[UUID] | None = Field(default=None, max_length=20)


class ClaimStart(ClaimData):
    patient_profile_id: UUID
    request: str = Field(default="Prepare insurance claim", min_length=1, max_length=10000)
    use_graph: bool = Field(default=False, strict=True)


class ClaimUpdate(ClaimData):
    expected_version: int = Field(ge=0, strict=True)


class CaseStatus(Contract):
    workflow: WorkflowRun
    kind: Literal["onboarding", "insurance_claim"]
    subject_user_id: str
    entity_id: UUID
    phase: Literal["waiting_data", "collecting", "awaiting_approval", "confirmed", "failed", "cancelled", "rejected"]
    missing_fields: list[str]
    next_action: str | None


class PatientProfile(Contract):
    id: UUID
    owner_user_id: str
    onboarding_workflow_id: UUID
    full_name: str | None
    date_of_birth: date | None
    contact_email: EmailStr
    phone: str | None
    external_patient_id: str | None
    confirmation_call_id: UUID | None
    status: Literal["draft", "active", "cancelled", "failed", "rejected"]
    created_at: datetime
    updated_at: datetime


class ClaimRecord(Contract):
    id: UUID
    workflow_id: UUID
    patient_profile_id: UUID
    owner_user_id: str
    policy_id: str | None
    procedure_code: str | None
    amount: Decimal | None
    currency: str
    status: Literal["waiting_for_data", "preparing", "prepared", "submitted", "failed", "cancelled", "rejected"]
    external_claim_id: str | None
    submission_call_id: UUID | None
    created_at: datetime
    updated_at: datetime


class AppointmentCreate(Contract):
    patient_profile_id: UUID
    doctor_reference: str = Field(min_length=1, max_length=100)
    starts_at: datetime
    duration_minutes: int = Field(default=30, ge=5, le=240, strict=True)

    @field_validator("starts_at")
    @classmethod
    def future_aware(cls, value):
        if value.tzinfo is None or value <= datetime.now(timezone.utc):
            raise ValueError("Appointment time must be future and include a timezone")
        return value.astimezone(timezone.utc)


class AppointmentRecord(AppointmentCreate):
    # Stored historical drafts must remain readable after their requested time.
    @field_validator("starts_at")
    @classmethod
    def future_aware(cls, value):
        return value

    id: UUID
    owner_user_id: str
    status: Literal["draft"]
    created_at: datetime
