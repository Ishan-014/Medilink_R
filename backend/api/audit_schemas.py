from datetime import datetime
from typing import Generic, Literal, TypeVar
from uuid import UUID
from pydantic import JsonValue
from backend.api.schemas import AuditEvent, Contract, WorkflowRun

T = TypeVar("T")


class AuditRecord(AuditEvent):
    sequence: int
    request_id: str | None
    metadata: dict[str, JsonValue]


class SystemEvent(Contract):
    id: UUID
    workflow_id: UUID | None
    request_id: str | None
    actor_user_id: str
    component: Literal["api", "database", "mcp", "graph", "ai", "recovery"]
    code: str
    severity: Literal["info", "warning", "error"]
    http_status: int | None
    duration_ms: int | None
    created_at: datetime


class Page(Contract, Generic[T]):
    items: list[T]
    next_offset: int | None


class TraceEvent(Contract):
    id: UUID
    created_at: datetime
    source: Literal["audit", "system"]
    actor_user_id: str
    request_id: str | None
    action: str
    resource_type: str
    resource_id: str
    outcome: Literal["success", "denied", "failure"]
    metadata: dict[str, JsonValue]


class WorkflowTrace(Contract):
    workflow: WorkflowRun
    events: list[TraceEvent]
    next_offset: int | None
