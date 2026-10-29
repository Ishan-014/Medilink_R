from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class RequestContext:
    request_id: str
    actor_user_id: str = "anonymous"


# The mutable context object is shared with sync dependency thread copies.
request_context: ContextVar[RequestContext | None] = ContextVar(
    "medilink_request", default=None
)
