from typing import Any, Dict, List

from langgraph.graph import StateGraph, END

from backend.agents.state import AgentState
from backend.agents.nodes import choose_tool, execute_tool, generate_final_response


def build_graph():
    workflow = StateGraph(AgentState)

    workflow.add_node("choose_tool", choose_tool)
    workflow.add_node("execute_tool", execute_tool)
    workflow.add_node("generate_final_response", generate_final_response)

    workflow.set_entry_point("choose_tool")
    workflow.add_edge("choose_tool", "execute_tool")
    workflow.add_edge("execute_tool", "generate_final_response")
    workflow.add_edge("generate_final_response", END)

    return workflow.compile()


def run_agent(user_message: str) -> str:
    graph = build_graph()
    result = graph.invoke({"user_message": user_message})
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    return result.get("final_response", "I could not answer that request.")
