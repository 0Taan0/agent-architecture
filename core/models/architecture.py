"""The spine of the system. Pure Pydantic, no IO.

Design rules (Schritt 1 of the build guide):
- provenance on every element
- declared and confirmed kept separate (needed for Declaration Drift)
- source_adapter on every element (needed to debug the merge)
- unresolved list = "incomplete yes, wrong no"
- code_ref optional (greenfield design has no code yet)
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Provenance(str, Enum):
    CONFIRMED = "confirmed"   # structurally read from the code
    DECLARED = "declared"     # annotated by the developer
    INFERRED = "inferred"     # heuristically derived
    UNKNOWN = "unknown"       # recognised, but not attributable


PROVENANCE_RANK = {
    Provenance.CONFIRMED: 3,
    Provenance.DECLARED: 2,
    Provenance.INFERRED: 1,
    Provenance.UNKNOWN: 0,
}


class CodeRef(BaseModel):
    file: str                 # relative to project root, forward slashes
    symbol: str | None = None
    line: int | None = None


class SchemaField(BaseModel):
    name: str
    type: str
    optional: bool = False


class SchemaDef(BaseModel):
    id: str
    name: str
    fields: list[SchemaField] = Field(default_factory=list)
    code_ref: CodeRef | None = None
    provenance: Provenance = Provenance.UNKNOWN
    source_adapter: str = "manual"


class ModelConfig(BaseModel):
    provider: str | None = None
    model: str | None = None          # pinned snapshot where the provider offers one
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    seed: int | None = None


class AgentNode(BaseModel):
    id: str                          # agent_7f31a0b - stable, derived from file + symbol
    name: str
    description: str | None = None
    model: ModelConfig | None = None
    input_schema: str | None = None  # SchemaDef.id
    output_schema: str | None = None
    tools: list[str] = Field(default_factory=list)
    code_ref: CodeRef | None = None
    provenance: Provenance = Provenance.UNKNOWN
    source_adapter: str = "manual"


class Connection(BaseModel):
    id: str
    source: str                      # AgentNode.id (or "__start__" / "__end__")
    target: str
    condition: str | None = None
    provenance: Provenance = Provenance.UNKNOWN
    source_adapter: str = "manual"


class UnresolvedItem(BaseModel):
    kind: str                        # e.g. dynamic_tool_registration, import_failed
    location: str | None = None      # file:line
    message: str | None = None
    source_adapter: str = "manual"


class ArchitectureModel(BaseModel):
    version: str = "0.1"
    name: str
    agents: list[AgentNode] = Field(default_factory=list)
    schemas: list[SchemaDef] = Field(default_factory=list)
    connections: list[Connection] = Field(default_factory=list)
    unresolved: list[UnresolvedItem] = Field(default_factory=list)

    # --- lookup helpers (no IO) -------------------------------------------------
    def agent(self, agent_id: str) -> AgentNode | None:
        return next((a for a in self.agents if a.id == agent_id), None)

    def agent_by_name(self, name: str) -> AgentNode | None:
        low = name.lower()
        return next((a for a in self.agents if a.name.lower() == low), None)

    def schema(self, schema_id: str) -> SchemaDef | None:
        return next((s for s in self.schemas if s.id == schema_id), None)

    def schema_by_name(self, name: str) -> SchemaDef | None:
        return next((s for s in self.schemas if s.name == name), None)

    def sorted(self) -> ArchitectureModel:
        """Deterministic ordering: required for stable YAML and stable hashes."""
        return self.model_copy(update={
            "agents": sorted(self.agents, key=lambda a: a.id),
            "schemas": sorted(self.schemas, key=lambda s: s.id),
            "connections": sorted(self.connections, key=lambda c: c.id),
            "unresolved": sorted(self.unresolved, key=lambda u: (u.kind, u.location or "")),
        })


class NodePosition(BaseModel):
    x: float
    y: float


class LayoutModel(BaseModel):
    """Canvas positions live in their own file so moving a node never causes drift."""
    positions: dict[str, NodePosition] = Field(default_factory=dict)
