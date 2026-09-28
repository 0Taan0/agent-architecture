"""Turn the subprocess result (compiled graph -> get_graph()) into architecture elements.
Provenance: confirmed - this is the truth, not an estimate."""
from __future__ import annotations

from core.inspector.base import ExtractionResult
from core.inspector.runner import IntrospectionResult
from core.models import AgentNode, Connection, Provenance, UnresolvedItem, connection_id, stable_id

SPECIAL = {"__start__", "__end__"}
ADAPTER = "langgraph"


def topology_to_result(intro: IntrospectionResult) -> tuple[ExtractionResult, dict[str, str]]:
    """Returns (result, name->agent_id map) so the AST pass can enrich the same nodes."""
    result = ExtractionResult()
    name_to_id: dict[str, str] = {}
    for graph in intro.graphs:
        module, symbol = graph.get("module", "?"), graph.get("symbol", "?")
        file_hint = module.replace(".", "/") + ".py"
        for n in graph.get("nodes", []):
            nid = n["id"]
            if nid in SPECIAL:
                continue
            aid = stable_id("agent", file_hint, f"node:{nid}")   # same key as AST fallback
            name_to_id[nid] = aid
            result.agents.append(AgentNode(
                id=aid, name=nid, provenance=Provenance.CONFIRMED, source_adapter=ADAPTER,
                description=f"LangGraph node in {module}.{symbol}"))
        for e in graph.get("edges", []):
            src = name_to_id.get(e["source"], e["source"])
            tgt = name_to_id.get(e["target"], e["target"])
            cond = None
            if e.get("conditional"):
                cond = str(e.get("data")) if e.get("data") not in (None, "") else "conditional"
            result.connections.append(Connection(
                id=connection_id(src, tgt, cond), source=src, target=tgt, condition=cond,
                provenance=Provenance.CONFIRMED, source_adapter=ADAPTER))
        if graph.get("state_schema"):
            result.notes.append(f"{module}.{symbol}: state schema {graph['state_schema']}")
    for err in intro.errors:
        result.unresolved.append(UnresolvedItem(kind="introspection_error", message=err,
                                                source_adapter=ADAPTER))
    if intro.framework_versions:
        result.notes.append("langgraph " + intro.framework_versions.get("langgraph", "?"))
    return result, name_to_id
