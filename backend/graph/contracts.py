from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, model_validator

from backend.api.schemas import Contract

Operation = Literal["query_relationships", "find_dependencies", "claim_history"]
GRAPH_OPERATIONS = ("query_relationships", "find_dependencies", "claim_history")
Reference = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")]


class Relationship(Contract):
    kind: Literal["relationship"]
    source_reference: Reference
    relation: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    target_reference: Reference


class Dependency(Contract):
    kind: Literal["dependency"]
    reference: Reference
    resolved: StrictBool


class ClaimHistory(Contract):
    kind: Literal["claim"]
    reference: Reference
    outcome: Literal["approved", "rejected", "pending", "unknown"]


Item = Annotated[Relationship | Dependency | ClaimHistory, Field(discriminator="kind")]


class GraphRequest(Contract):
    query_id: UUID
    workflow_id: UUID
    operation: Operation
    patient_id: str = Field(min_length=1, max_length=128)
    policy_id: str | None = Field(default=None, max_length=100)
    procedure_code: str | None = Field(default=None, max_length=100)
    max_results: int = Field(ge=1, le=200)


class GraphResult(Contract):
    query_id: UUID
    operation: Operation
    patient_id: str = Field(min_length=1, max_length=128)
    result_reference: str = Field(pattern=r"^graph-result:[A-Za-z0-9._-]{1,128}$")
    graph_revision: Reference
    complete: StrictBool
    items: list[Item] = Field(max_length=200)

    @model_validator(mode="after")
    def coherent_items(self):
        expected = {"query_relationships": "relationship", "find_dependencies": "dependency", "claim_history": "claim"}[self.operation]
        if any(item.kind != expected for item in self.items):
            raise ValueError("Wrong item type for graph operation")
        keys = [item.model_dump_json() for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate graph items")
        return self


class GraphQueryInput(Contract):
    expected_version: int = Field(ge=0, strict=True)
    step_id: UUID
    patient_profile_id: UUID
    operation: Operation


class GraphQueryRecord(Contract):
    id: UUID
    workflow_id: UUID
    workflow_step_id: UUID
    patient_profile_id: UUID
    actor_user_id: str
    operation: Operation
    status: Literal["pending", "succeeded", "failed"]
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    error_code: str | None
    result_reference: str | None
    graph_revision: str | None
    item_count: int | None
    unresolved_dependencies: int | None


class GraphQueryOutput(Contract):
    query: GraphQueryRecord
    result: GraphResult
