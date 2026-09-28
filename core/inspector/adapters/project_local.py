"""project_local adapter - the user describes their own pattern once in
`.architecture/inspect.py`:

    def discover(ctx):
        for cls in ctx.classes_inheriting("core.base.BaseAgent"):
            yield ctx.agent(...)

The file is always User-Owned. The platform only ever proposes a scaffold.
The script runs in-process (it is the user's own inspection logic, working on the
AST through ctx), but the user's PROJECT code is never imported.
"""
from __future__ import annotations

import types

from core.inspector.base import Confidence, ExtractionResult
from core.inspector.context import InspectContext
from core.models import AgentNode, Connection, ProjectContext, Provenance, SchemaDef, UnresolvedItem

INSPECT_FILE = ".architecture/inspect.py"


class ProjectLocalAdapter:
    name = "project_local"
    supported_versions = None

    def detect(self, project: ProjectContext) -> Confidence:
        return Confidence.STRONG if (project.root / INSPECT_FILE).exists() else Confidence.NONE

    def extract(self, project: ProjectContext) -> ExtractionResult:
        result = ExtractionResult()
        path = project.root / INSPECT_FILE
        ctx = InspectContext(project, adapter=self.name, provenance=Provenance.DECLARED)
        module = types.ModuleType("architecture_inspect")
        module.__file__ = str(path)
        try:
            code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
            exec(code, module.__dict__)  # noqa: S102 - user's own inspect.py, by design
            discover = module.discover
            items = list(discover(ctx) or [])
        except Exception as exc:  # noqa: BLE001 - surface, never hide
            result.unresolved.append(UnresolvedItem(
                kind="inspect_script_failed", location=INSPECT_FILE,
                message=f"{type(exc).__name__}: {exc}", source_adapter=self.name))
            return result
        for item in items:
            if isinstance(item, AgentNode):
                result.agents.append(item)
            elif isinstance(item, SchemaDef):
                result.schemas.append(item)
            elif isinstance(item, Connection):
                result.connections.append(item)
            elif isinstance(item, UnresolvedItem):
                result.unresolved.append(item)
            else:
                result.notes.append(f"discover() yielded unsupported item {type(item).__name__}")
        for sd in ctx.discovered_schemas():
            if not any(s.id == sd.id for s in result.schemas):
                result.schemas.append(sd)
        result.unresolved.extend(ctx.pending_unresolved())
        return result


SCAFFOLD = '''"""Project-local inspector for agent-architect. This file is yours.

Describe how agents look in THIS project. Runs statically on the AST via `ctx`.
"""


def discover(ctx):
    for cls in ctx.classes_named("Agent"):
        if cls.name.startswith("Base"):
            continue
        yield ctx.agent(
            id=ctx.stable_id(cls),
            name=cls.name,
            input_schema=ctx.first_param_annotation(cls, "run"),
            output_schema=ctx.return_annotation(cls, "run"),
            tools=ctx.literal_list(cls, "TOOLS"),
            code_ref=ctx.ref(cls),
        )
'''
