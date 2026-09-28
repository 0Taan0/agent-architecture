"""inspect_project: run every applicable adapter, merge, report who contributed what."""
from __future__ import annotations

from pydantic import BaseModel, Field

from core.inspector.adapters import ALL_ADAPTERS
from core.inspector.base import Adapter, Confidence
from core.inspector.merge import MergeInput, merge
from core.models import ArchitectureModel, ProjectContext


class AdapterStatus(BaseModel):
    name: str
    confidence: Confidence
    agents: int = 0
    schemas: int = 0
    connections: int = 0
    unresolved: int = 0
    notes: list[str] = Field(default_factory=list)
    ran: bool = False


class InspectionReport(BaseModel):
    model: ArchitectureModel
    adapters: list[AdapterStatus]


def inspect_project(project: ProjectContext, adapters: list[Adapter] | None = None,
                    *, run_weak: bool = True) -> InspectionReport:
    adapters = adapters if adapters is not None else [cls() for cls in ALL_ADAPTERS]
    statuses: list[AdapterStatus] = []
    inputs: list[MergeInput] = []
    strong_found = False
    for adapter in adapters:
        conf = adapter.detect(project)
        st = AdapterStatus(name=adapter.name, confidence=conf)
        statuses.append(st)
        if conf == Confidence.STRONG:
            strong_found = True
    for adapter, st in zip(adapters, statuses):
        if st.confidence == Confidence.NONE:
            continue
        # conventions only run when nothing stronger claims the project, or on request
        if st.confidence == Confidence.WEAK and strong_found and not run_weak:
            continue
        result = adapter.extract(project)
        st.ran = True
        st.agents, st.schemas = len(result.agents), len(result.schemas)
        st.connections, st.unresolved = len(result.connections), len(result.unresolved)
        st.notes = result.notes
        inputs.append(MergeInput(adapter=adapter.name, result=result))
    model = merge(project.display_name, inputs)
    return InspectionReport(model=model, adapters=statuses)
