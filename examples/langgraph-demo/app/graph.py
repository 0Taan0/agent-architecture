"""Minimal LangGraph app. Introspected via subprocess if langgraph is installed in the
project's venv; otherwise the AST fallback yields an inferred topology."""
from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    question: str
    answer: str
    verified: bool


def research(state: State) -> dict:
    return {"answer": f"about {state['question']}"}


def verify(state: State) -> dict:
    return {"verified": len(state["answer"]) > 3}


def route(state: State) -> str:
    return "done" if state["verified"] else "research"


builder = StateGraph(State)
builder.add_node("research", research)
builder.add_node("verify", verify)
builder.add_edge(START, "research")
builder.add_edge("research", "verify")
builder.add_conditional_edges("verify", route, {"done": END, "research": "research"})
graph = builder.compile()
