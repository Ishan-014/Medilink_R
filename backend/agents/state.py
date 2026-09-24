from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AgentState(BaseModel):
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    user_message: str = ""
    tool_name: Optional[str] = None
    tool_arguments: Dict[str, Any] = Field(default_factory=dict)
    tool_result: Optional[Any] = None
    final_response: str = ""
