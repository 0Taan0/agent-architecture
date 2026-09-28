"""conventions adapter - folder `agents/`, class name ends with `Agent`, method `run`.
Suggestion mode only: everything is `inferred`; the GUI asks instead of asserting.
Useful as a starting point from which the user generates declarations or inspect.py."""
from __future__ import annotations

from core.inspector.base import Confidence, ExtractionResult
from core.inspector.context import InspectContext
from core.models import ProjectContext, Provenance


class ConventionsAdapter:
    name = "conventions"
    supported_versions = None

    def detect(self, project: ProjectContext) -> Confidence:
        if (project.root / "agents").is_dir():
            return Confidence.WEAK
        ctx = InspectContext(project, adapter=self.name)
        return Confidence.WEAK if self._candidates(ctx) else Confidence.NONE

    def extract(self, project: ProjectContext) -> ExtractionResult:
        ctx = InspectContext(project, adapter=self.name, provenance=Provenance.INFERRED)
        result = ExtractionResult(notes=["conventions: results are inferred, confirm them "
                                        "via @agent or .architecture/inspect.py"])
        for cls in self._candidates(ctx):
            result.agents.append(ctx.agent(
                id=ctx.stable_id(cls), name=cls.name,
                input_schema=ctx.first_param_annotation(cls, "run"),
                output_schema=ctx.return_annotation(cls, "run"),
                tools=ctx.literal_list(cls, "TOOLS") or ctx.literal_list(cls, "tools"),
                code_ref=ctx.ref(cls)))
        result.schemas.extend(ctx.discovered_schemas())
        result.unresolved.extend(ctx.pending_unresolved())
        return result

    @staticmethod
    def _candidates(ctx: InspectContext):
        return [c for c in ctx.classes_named("Agent")
                if not c.name.startswith("Base") and c.method("run") is not None]
