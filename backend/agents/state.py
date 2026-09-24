from __future__ import annotations
from typing import Any, Dict, List
from pydantic import BaseModel, Field

class AgentState(BaseModel):
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    user_message: str = ""
    history: List[Dict[str, Any]] = Field(default_factory=list)
    final_response: str = ""
