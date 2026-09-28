"""Adapter protocol. All four mechanisms (declarations, project_local, conventions,
langgraph) go through this one door."""
from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from core.models import AgentNode, Connection, ProjectContext, SchemaDef, UnresolvedItem


class Confidence(str, Enum):
    NONE = "none"
    WEAK = "weak"
    STRONG = "strong"


class ExtractionResult(BaseModel):
    agents: list[AgentNode] = Field(default_factory=list)
    schemas: list[SchemaDef] = Field(default_factory=list)
    connections: list[Connection] = Field(default_factory=list)
    unresolved: list[UnresolvedItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)   # "version 0.5 not tested"

    def is_empty(self) -> bool:
        return not (self.agents or self.schemas or self.connections)


@runtime_checkable
class Adapter(Protocol):
    name: str
    supported_versions: str | None

    def detect(self, project: ProjectContext) -> Confidence: ...

    def extract(self, project: ProjectContext) -> ExtractionResult: ...
