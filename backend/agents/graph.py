from typing import Any, Dict
from langgraph.graph import StateGraph, END
from backend.agents.state import AgentState
from backend.agents.nodes import run_agent

def agent_node(state: AgentState) -> Dict[str, Any]:
    return {"final_response": run_agent(state.user_message, state.history)}

workflow = StateGraph(AgentState)
workflow.add_node("agent", agent_node)
workflow.set_entry_point("agent")
workflow.add_edge("agent", END)
_graph = workflow.compile()

def run_conversation(user_message: str, history=None) -> str:
    result = _graph.invoke({"user_message": user_message, "history": history or []})
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    return result.get("final_response", "I could not answer that request.")
