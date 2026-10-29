"""Shared wire contracts. Workflow/auth/document/audit endpoints follow later."""

from datetime import datetime
from enum import Enum
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SuccessResponse(Contract, Generic[T]):
    data: T
    request_id: str


class ErrorDetail(Contract):
    code: str
    message: str
    details: list[dict[str, Any]] = Field(default_factory=list)


class ErrorResponse(Contract):
    error: ErrorDetail
    request_id: str


class HealthData(Contract):
    status: Literal["ok", "ready"]
    service: Literal["medilink-backend"] = "medilink-backend"


class Role(str, Enum):
    PATIENT = "patient"
    DOCTOR = "doctor"
    RECEPTIONIST = "receptionist"
    ADMIN = "admin"
    AUDITOR = "auditor"


class CurrentUser(Contract):
    user_id: str
    role: Role
    email: str


class WorkflowStatus(str, Enum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    WAITING_FOR_DATA = "WAITING_FOR_DATA"
    EXECUTING = "EXECUTING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class WorkflowType(str, Enum):
    ONBOARDING = "onboarding"
    INSURANCE_CLAIM = "insurance_claim"
    APPOINTMENT = "appointment"


class RiskTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class WorkflowCreate(Contract):
    workflow_type: WorkflowType
    request: str = Field(min_length=1, max_length=10000)
    document_ids: list[UUID] = Field(default_factory=list, max_length=20)


class WorkflowStep(Contract):
    id: UUID
    position: int = Field(ge=0)
    action: str = Field(min_length=1, max_length=200)
    status: WorkflowStatus
    risk: RiskTier | None = None
    result_reference: str | None = None


class WorkflowRun(Contract):
    id: UUID
    owner_user_id: str
    workflow_type: WorkflowType
    status: WorkflowStatus
    created_at: datetime
    updated_at: datetime
    version: int = Field(default=0, ge=0)
    steps: list[WorkflowStep] = Field(default_factory=list)


class DocumentMetadata(Contract):
    id: UUID
    filename: str
    media_type: str
    size_bytes: int = Field(ge=0)
    extraction_status: Literal["pending", "processing", "completed", "failed"]
    created_at: datetime


class AuditEvent(Contract):
    id: UUID
    workflow_id: UUID | None = None
    actor_user_id: str
    action: str
    resource_type: str
    resource_id: str
    outcome: Literal["success", "denied", "failure"]
    created_at: datetime
