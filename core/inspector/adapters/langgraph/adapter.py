"""LangGraph adapter (hybrid):

  subprocess in the project's venv -> get_graph() -> topology       confirmed
  AST pass over the same files      -> lines, schemas, tools, model  confirmed / inferred
  merge by node name

If the import fails (missing deps, API keys, side effects) we fall back to an AST-only
topology that is explicitly `inferred` and report the failure as unresolved.
"""
from __future__ import annotations

from core.inspector.base import Confidence, ExtractionResult
from core.inspector.context import InspectContext
from core.inspector.runner import IntrospectionError, run_introspection
from core.models import ProjectContext, Provenance, UnresolvedItem

from . import ast_pass, topology

SUPPORTED = ">=0.2,<2"


class LangGraphAdapter:
    name = "langgraph"
    supported_versions = SUPPORTED

    def __init__(self, allow_subprocess: bool = True, timeout: int = 60) -> None:
        self.allow_subprocess = allow_subprocess
        self.timeout = timeout

    def detect(self, project: ProjectContext) -> Confidence:
        ctx = InspectContext(project, adapter=self.name)
        if not any("langgraph" in m.imports.get(k, "") for m in ctx.modules for k in m.imports):
            return Confidence.NONE
        return Confidence.STRONG if ast_pass.graph_modules(ctx) else Confidence.WEAK

    def extract(self, project: ProjectContext) -> ExtractionResult:
        ctx = InspectContext(project, adapter=self.name, provenance=Provenance.CONFIRMED)
        modules = ast_pass.graph_modules(ctx)
        if not self.allow_subprocess:
            return ast_pass.fallback_topology(ctx)
        try:
            intro = run_introspection(project, modules, timeout=self.timeout)
        except IntrospectionError as exc:
            result = ast_pass.fallback_topology(ctx)
            result.unresolved.append(UnresolvedItem(
                kind="import_failed", message=str(exc), source_adapter=self.name))
            return result
        if not intro.graphs:
            result = ast_pass.fallback_topology(ctx)
            for err in intro.errors:
                result.unresolved.append(UnresolvedItem(kind="import_failed", message=err,
                                                        source_adapter=self.name))
            return result
        result, name_to_id = topology.topology_to_result(intro)
        ast_pass.enrich(ctx, result, name_to_id)
        v = intro.framework_versions.get("langgraph")
        if v and not _version_supported(v):
            result.notes.append(f"langgraph {v} is outside the tested range {SUPPORTED}")
        result.unresolved.extend(ctx.pending_unresolved())
        return result


def _version_supported(v: str) -> bool:
    try:
        major = int(v.split(".")[0])
    except ValueError:
        return False
    return major < 2
