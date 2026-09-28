"""Impact graph: produces / consumes / routes_to from the architecture, field usages
from the code, plus the dimension no IDE has - which past runs used the old hash."""
from __future__ import annotations

from pydantic import BaseModel, Field

from core.models import ArchitectureModel, ProjectContext
from core.runtime.store import RunStore

from .field_usage import FieldUsage, find_field_usages


class Relation(BaseModel):
    kind: str          # produces | consumes | routes_to | uses_tool
    source: str
    target: str


class ImpactGraph(BaseModel):
    relations: list[Relation] = Field(default_factory=list)

    def neighbours(self, node: str, kinds: set[str] | None = None) -> list[Relation]:
        return [r for r in self.relations if (r.source == node or r.target == node)
                and (kinds is None or r.kind in kinds)]


class ImpactReport(BaseModel):
    schema_name: str
    direct_agents: list[str] = Field(default_factory=list)
    indirect_agents: list[str] = Field(default_factory=list)
    field_usages: dict[str, list[FieldUsage]] = Field(default_factory=dict)
    affected_runs: int = 0
    affected_run_ids: list[str] = Field(default_factory=list)


def build_impact_graph(model: ArchitectureModel) -> ImpactGraph:
    g = ImpactGraph()
    for a in model.agents:
        if a.input_schema:
            g.relations.append(Relation(kind="consumes", source=a.id, target=a.input_schema))
        if a.output_schema:
            g.relations.append(Relation(kind="produces", source=a.id, target=a.output_schema))
        for t in a.tools:
            g.relations.append(Relation(kind="uses_tool", source=a.id, target=t))
    for c in model.connections:
        g.relations.append(Relation(kind="routes_to", source=c.source, target=c.target))
    return g


def impact_of_schema(project: ProjectContext, model: ArchitectureModel, schema_id: str,
                     fields: list[str] | None = None,
                     previous_architecture_hash: str | None = None) -> ImpactReport:
    schema = model.schema(schema_id)
    if schema is None:
        raise KeyError(schema_id)
    g = build_impact_graph(model)
    direct = {r.source for r in g.relations if r.target == schema_id and r.kind in ("produces", "consumes")}
    indirect: set[str] = set()
    frontier = list(direct)
    while frontier:
        cur = frontier.pop()
        for r in g.relations:
            if r.kind == "routes_to" and r.source == cur and r.target not in direct | indirect:
                indirect.add(r.target)
                frontier.append(r.target)
    report = ImpactReport(schema_name=schema.name,
                          direct_agents=sorted(_name(model, i) for i in direct),
                          indirect_agents=sorted(_name(model, i) for i in indirect))
    for f in fields or [fl.name for fl in schema.fields]:
        report.field_usages[f] = find_field_usages(project, f)
    if previous_architecture_hash:
        store = RunStore(project.root)
        ids = store.run_ids_for_hash(previous_architecture_hash)
        report.affected_runs, report.affected_run_ids = len(ids), ids
    return report


def _name(model: ArchitectureModel, aid: str) -> str:
    a = model.agent(aid)
    return a.name if a else aid
