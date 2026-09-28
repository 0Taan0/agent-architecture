"""Traces are JSONL, one event per line, OpenTelemetry GenAI field names."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

TRACES_DIR = ".architecture/traces"


class TraceEvent(BaseModel):
    event: str
    agent: str | None = None
    ts: float | None = None
    duration_ms: float | None = None
    data: dict = Field(default_factory=dict)


def trace_path(root: Path, run_id: str) -> Path:
    p = Path(root) / TRACES_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{run_id}.jsonl"


def read_trace(path: Path) -> list[TraceEvent]:
    if not Path(path).exists():
        return []
    events = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        events.append(TraceEvent(event=raw.pop("event", "?"), agent=raw.pop("agent", None),
                                 ts=raw.pop("ts", None), duration_ms=raw.pop("duration_ms", None),
                                 data=raw))
    return events


def summarize_trace(events: list[TraceEvent]) -> dict:
    """Per-agent duration, token usage, model served - the RUN #42 view of Spec ch. 10."""
    agents: dict[str, dict] = {}
    served: set[str] = set()
    for e in events:
        if e.agent is None:
            continue
        a = agents.setdefault(e.agent, {"duration_ms": None, "input_tokens": 0,
                                        "output_tokens": 0, "llm_calls": 0, "error": None})
        if e.event == "agent.end":
            a["duration_ms"] = e.duration_ms
        elif e.event == "agent.error":
            a["error"], a["duration_ms"] = e.data.get("error"), e.duration_ms
        elif e.event == "gen_ai.call":
            a["llm_calls"] += 1
            a["input_tokens"] += e.data.get("gen_ai.usage.input_tokens") or 0
            a["output_tokens"] += e.data.get("gen_ai.usage.output_tokens") or 0
            if e.data.get("gen_ai.response.model"):
                served.add(e.data["gen_ai.response.model"])
    order = [e.agent for e in events if e.event == "agent.start" and e.agent]
    return {"order": order, "agents": agents, "model_served": sorted(served)}
