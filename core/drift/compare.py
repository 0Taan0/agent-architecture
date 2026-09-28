"""Drift = stored architecture (intent) vs inspected code (fact).

    current = inspector.inspect(project)     # from the code
    stored  = load_architecture(project)     # from architecture.yaml
    diffs   = compare(stored, current)

Import is the same comparison against an empty architecture (Spec 8.5).
The code is the truth about what IS; the architecture is the truth about what is INTENDED.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from core.models import ArchitectureModel, ProjectContext
from core.ownership import Manifest, file_hash

from .classify import Severity, severity_of


class DriftItem(BaseModel):
    kind: str
    severity: Severity
    subject: str                      # agent/schema name or file path
    node_id: str | None = None
    field: str | None = None
    architecture: str | None = None   # what the architecture says
    code: str | None = None           # what the code says
    location: str | None = None


class DriftReport(BaseModel):
    items: list[DriftItem] = Field(default_factory=list)

    @property
    def breaking(self) -> list[DriftItem]:
        return [i for i in self.items if i.severity == Severity.BREAKING]

    def visible(self) -> list[DriftItem]:
        """Cosmetic drift is never shown (false alarms kill the feature)."""
        return [i for i in self.items if i.severity != Severity.COSMETIC]


def _item(kind: str, subject: str, **kw) -> DriftItem:
    return DriftItem(kind=kind, severity=severity_of(kind), subject=subject, **kw)


def compare(stored: ArchitectureModel, current: ArchitectureModel) -> DriftReport:
    report = DriftReport()
    s_agents = {a.id: a for a in stored.agents}
    c_agents = {a.id: a for a in current.agents}
    c_by_name = {a.name.lower(): a for a in current.agents}

    for aid, sa in s_agents.items():
        ca = c_agents.get(aid)
        if ca is None:
            renamed = c_by_name.get(sa.name.lower())
            if renamed is not None and renamed.id not in s_agents:
                report.items.append(_item("agent_renamed", sa.name, node_id=aid,
                                          architecture=aid, code=renamed.id))
                ca = renamed
            else:
                report.items.append(_item("agent_removed_in_code", sa.name, node_id=aid,
                                          location=sa.code_ref.file if sa.code_ref else None))
                continue
        _compare_agent(sa, ca, stored, current, report)

    for aid, ca in c_agents.items():
        if aid not in s_agents and ca.name.lower() not in {a.name.lower() for a in stored.agents}:
            report.items.append(_item("agent_added_in_code", ca.name, node_id=aid,
                                      location=ca.code_ref.file if ca.code_ref else None,
                                      code=ca.provenance.value))

    _compare_schemas(stored, current, report)

    s_conn = {(c.source, c.target): c for c in stored.connections}
    c_conn = {(c.source, c.target): c for c in current.connections}
    for key, c in s_conn.items():
        if key not in c_conn:
            report.items.append(_item("connection_removed_in_code",
                                      f"{_n(stored, c.source)} -> {_n(stored, c.target)}"))
    for key, c in c_conn.items():
        if key not in s_conn and current.agent(c.source) and current.agent(c.target):
            report.items.append(_item("connection_added_in_code",
                                      f"{_n(current, c.source)} -> {_n(current, c.target)}"))

    for u in current.unresolved:
        if u.kind in ("declaration_drift", "adapter_conflict"):
            report.items.append(_item(u.kind, u.message or u.kind, location=u.location))
    return report


def _n(m: ArchitectureModel, aid: str) -> str:
    a = m.agent(aid)
    return a.name if a else aid


def _compare_agent(sa, ca, stored, current, report: DriftReport) -> None:
    for t in set(sa.tools) - set(ca.tools):
        report.items.append(_item("tool_removed_in_code", sa.name, node_id=sa.id, field="tools",
                                  architecture=t, code=", ".join(ca.tools) or "-"))
    for t in set(ca.tools) - set(sa.tools):
        report.items.append(_item("tool_added_in_code", sa.name, node_id=sa.id, field="tools",
                                  architecture=", ".join(sa.tools) or "-", code=t))
    for fld in ("input_schema", "output_schema"):
        sv, cv = getattr(sa, fld), getattr(ca, fld)
        sn = stored.schema(sv).name if sv and stored.schema(sv) else sv
        cn = current.schema(cv).name if cv and current.schema(cv) else cv
        if sn != cn and cv is not None:   # code says nothing -> not a fact, no drift
            report.items.append(_item("io_schema_changed", sa.name, node_id=sa.id, field=fld,
                                      architecture=str(sn), code=str(cn)))
    if sa.model and ca.model and sa.model.model and ca.model.model and \
            sa.model.model != ca.model.model:
        report.items.append(_item("model_changed", sa.name, node_id=sa.id, field="model",
                                  architecture=sa.model.model, code=ca.model.model))
    if sa.code_ref and ca.code_ref and sa.code_ref.file != ca.code_ref.file:
        report.items.append(_item("code_ref_moved", sa.name, node_id=sa.id,
                                  architecture=sa.code_ref.file, code=ca.code_ref.file))


def _compare_schemas(stored, current, report: DriftReport) -> None:
    s_by = {s.name: s for s in stored.schemas}
    c_by = {s.name: s for s in current.schemas}
    for name, ss in s_by.items():
        cs = c_by.get(name)
        if cs is None:
            report.items.append(_item("schema_removed_in_code", name, node_id=ss.id))
            continue
        sf = {f.name: f for f in ss.fields}
        cf = {f.name: f for f in cs.fields}
        for fn in sf.keys() - cf.keys():
            report.items.append(_item("schema_field_removed", name, node_id=ss.id, field=fn,
                                      architecture=sf[fn].type, code="-",
                                      location=_loc(cs)))
        for fn in cf.keys() - sf.keys():
            report.items.append(_item("schema_field_added", name, node_id=ss.id, field=fn,
                                      architecture="-", code=cf[fn].type, location=_loc(cs)))
        for fn in sf.keys() & cf.keys():
            if sf[fn].type.replace(" ", "") != cf[fn].type.replace(" ", ""):
                report.items.append(_item("schema_field_type_changed", name, node_id=ss.id,
                                          field=fn, architecture=sf[fn].type, code=cf[fn].type,
                                          location=_loc(cs)))
    for name, cs in c_by.items():
        if name not in s_by:
            report.items.append(_item("schema_added_in_code", name, node_id=cs.id,
                                      location=_loc(cs)))


def _loc(s) -> str | None:
    return f"{s.code_ref.file}:{s.code_ref.line}" if s.code_ref else None


def file_drift(project: ProjectContext, manifest: Manifest) -> DriftReport:
    """Generated files edited by hand or missing - via manifest content hashes."""
    report = DriftReport()
    for rel, entry in manifest.generated_files().items():
        path = project.abs(rel)
        if not path.exists():
            report.items.append(_item("generated_file_missing", rel, node_id=entry.node_id))
        elif entry.content_hash and file_hash(path) != entry.content_hash:
            report.items.append(_item("generated_file_modified", rel, node_id=entry.node_id,
                                      architecture=entry.content_hash, code=file_hash(path)))
    return report


def detect_drift(project: ProjectContext, stored: ArchitectureModel | None,
                 current: ArchitectureModel, manifest: Manifest) -> DriftReport:
    base = ArchitectureModel(name=current.name) if stored is None else stored
    report = compare(base, current)
    report.items.extend(file_drift(project, manifest).items)
    return report


__all__ = ["DriftItem", "DriftReport", "Path", "compare", "detect_drift", "file_drift"]
