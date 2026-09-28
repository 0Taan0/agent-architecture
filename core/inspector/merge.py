"""Merge results of several adapters into one ArchitectureModel.

Fixed priority: confirmed > declared > inferred > unknown.
Contradictions are NOT resolved silently - they are reported as `adapter_conflict`
unresolved items (Declaration Drift feeds on this in core.drift).
Matching: by stable id first, then by name (the LangGraph node name vs the class name a
project_local adapter reports).
"""
from __future__ import annotations

from dataclasses import dataclass

from core.inspector.base import ExtractionResult
from core.models import (
    AgentNode,
    ArchitectureModel,
    Connection,
    SchemaDef,
    UnresolvedItem,
    connection_id,
)
from core.models.architecture import PROVENANCE_RANK


@dataclass
class MergeInput:
    adapter: str
    result: ExtractionResult


def merge(name: str, inputs: list[MergeInput]) -> ArchitectureModel:
    agents: dict[str, AgentNode] = {}
    by_name: dict[str, str] = {}
    schemas: dict[str, SchemaDef] = {}
    conflicts: list[UnresolvedItem] = []
    unresolved: list[UnresolvedItem] = []
    alias: dict[str, str] = {}   # id from a lower-priority adapter -> canonical id

    ordered = sorted(inputs, key=lambda i: -_max_rank(i.result))
    for inp in ordered:
        for a in inp.result.agents:
            key = agents.get(a.id) and a.id or by_name.get(a.name.lower())
            if key is None:
                agents[a.id] = a.model_copy(deep=True)
                by_name[a.name.lower()] = a.id
                continue
            alias[a.id] = key
            agents[key] = _merge_agent(agents[key], a, conflicts)
        for s in inp.result.schemas:
            if s.id not in schemas or PROVENANCE_RANK[s.provenance] > PROVENANCE_RANK[schemas[s.id].provenance]:
                schemas[s.id] = s
        unresolved.extend(inp.result.unresolved)

    connections: dict[str, Connection] = {}
    for inp in ordered:
        for c in inp.result.connections:
            src = _resolve_end(c.source, alias, by_name)
            tgt = _resolve_end(c.target, alias, by_name)
            if tgt.startswith("name:") or src.startswith("name:"):
                unresolved.append(UnresolvedItem(
                    kind="unresolved_connection_target", message=f"{c.source} -> {c.target}",
                    source_adapter=c.source_adapter))
                continue
            cid = connection_id(src, tgt, c.condition)
            cand = c.model_copy(update={"id": cid, "source": src, "target": tgt})
            if cid not in connections or \
                    PROVENANCE_RANK[cand.provenance] > PROVENANCE_RANK[connections[cid].provenance]:
                connections[cid] = cand

    return ArchitectureModel(name=name, agents=list(agents.values()), schemas=list(schemas.values()),
                             connections=list(connections.values()),
                             unresolved=unresolved + conflicts).sorted()


def _max_rank(r: ExtractionResult) -> int:
    return max((PROVENANCE_RANK[a.provenance] for a in r.agents), default=0)


def _resolve_end(end: str, alias: dict[str, str], by_name: dict[str, str]) -> str:
    if end.startswith("name:"):
        return by_name.get(end[5:].lower(), end)
    return alias.get(end, end)


def _merge_agent(high: AgentNode, low: AgentNode, conflicts: list[UnresolvedItem]) -> AgentNode:
    """`high` wins on conflict; missing fields are filled from `low`; conflicts recorded."""
    merged = high.model_copy(deep=True)
    for field_name in ("input_schema", "output_schema", "model", "code_ref", "description"):
        hv, lv = getattr(high, field_name), getattr(low, field_name)
        if hv is None and lv is not None:
            setattr(merged, field_name, lv)
        elif hv is not None and lv is not None and hv != lv:
            conflicts.append(UnresolvedItem(
                kind="adapter_conflict",
                location=(high.code_ref.file if high.code_ref else None),
                message=f"{high.name}.{field_name}: {high.source_adapter} says {_s(hv)}, "
                        f"{low.source_adapter} says {_s(lv)}",
                source_adapter=low.source_adapter))
    if not high.tools and low.tools:
        merged.tools = list(low.tools)
    elif high.tools and low.tools and set(high.tools) != set(low.tools):
        merged.tools = sorted(set(high.tools) | set(low.tools))
        conflicts.append(UnresolvedItem(
            kind="adapter_conflict", location=(high.code_ref.file if high.code_ref else None),
            message=f"{high.name}.tools: {high.source_adapter} {sorted(high.tools)} vs "
                    f"{low.source_adapter} {sorted(low.tools)}", source_adapter=low.source_adapter))
    if low.source_adapter not in merged.source_adapter.split("+"):
        merged.source_adapter = f"{merged.source_adapter}+{low.source_adapter}"
    return merged


def _s(v) -> str:
    return v.model_dump_json() if hasattr(v, "model_dump_json") else str(v)
