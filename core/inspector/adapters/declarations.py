"""declarations adapter - reads `@agent(...)` decorators or `__architecture__` class
attributes via AST. No execution. Provenance: declared.

The decorator itself lives in the USER's project (examples/snippets/architecture_decl.py):
eight lines, a runtime no-op, no dependency on this platform.
"""
from __future__ import annotations

import ast

from core.inspector.base import Confidence, ExtractionResult
from core.inspector.context import ClassInfo, InspectContext, _literal_list, _literal_str
from core.models import CodeRef, ModelConfig, ProjectContext, Provenance

DECORATOR = "agent"
ATTR = "__architecture__"


class DeclarationsAdapter:
    name = "declarations"
    supported_versions = None

    def detect(self, project: ProjectContext) -> Confidence:
        ctx = InspectContext(project, adapter=self.name)
        if ctx.classes_with_decorator(DECORATOR) or ctx.classes_with_attr(ATTR):
            return Confidence.STRONG
        return Confidence.NONE

    def extract(self, project: ProjectContext) -> ExtractionResult:
        ctx = InspectContext(project, adapter=self.name, provenance=Provenance.DECLARED)
        result = ExtractionResult()
        seen: set[str] = set()
        for cls in ctx.classes_with_decorator(DECORATOR) + ctx.classes_with_attr(ATTR):
            if cls.qualname in seen:
                continue
            seen.add(cls.qualname)
            decl = self._declaration(ctx, cls)
            code_ref = ctx.ref(cls)
            impl = decl.get("impl")           # generated bases point at the user's class
            if impl and ":" in impl:
                loc = ctx.find_symbol(impl.split(":")[1])
                if loc is not None:
                    code_ref = CodeRef(file=loc[0].rel, symbol=impl.split(":")[1],
                                       line=loc[1].lineno)
            agent = ctx.agent(
                id=decl.get("id") or ctx.stable_id(cls),
                name=decl.get("name") or cls.name,
                description=decl.get("description"),
                input_schema=decl.get("input") or ctx.first_param_annotation(cls, "run"),
                output_schema=decl.get("output") or ctx.return_annotation(cls, "run"),
                tools=decl.get("tools") or ctx.literal_list(cls, "TOOLS"),
                model=ModelConfig(model=decl["model"]) if decl.get("model") else None,
                code_ref=code_ref,
            )
            result.agents.append(agent)
            for target in decl.get("routes_to", []):
                # declared connections reference agents by *name*; resolved in merge
                result.connections.append(ctx.connection(agent.id, f"name:{target}"))
            # declared vs actually-annotated types: keep both, merge reports drift
            actual_out = ctx.return_annotation(cls, "run")
            if decl.get("output") and actual_out and \
                    _short(decl["output"]) != _short(actual_out):
                result.unresolved.append(ctx.unresolved(
                    "declaration_drift", f"{cls.file}:{cls.line}",
                    f"{agent.name}: declared output {decl['output']}, actual return {actual_out}"))
        result.schemas.extend(ctx.discovered_schemas())
        result.unresolved.extend(ctx.pending_unresolved())
        return result

    @staticmethod
    def _declaration(ctx: InspectContext, cls: ClassInfo) -> dict:
        decl: dict = {}
        call = ctx.decorator_call(cls, DECORATOR)
        if call is not None:
            if call.args:
                decl["name"] = _literal_str(call.args[0])
            for kw in call.keywords:
                if kw.arg in ("tools", "routes_to"):
                    decl[kw.arg] = _literal_list(kw.value)
                elif kw.arg in ("input", "output"):
                    decl[kw.arg] = _literal_str(kw.value) or ast.unparse(kw.value)
                else:
                    decl[kw.arg] = _literal_str(kw.value) or ast.unparse(kw.value)
        attr = cls.class_attr(ATTR)
        if isinstance(attr, ast.Dict):
            for k, v in zip(attr.keys, attr.values):
                key = _literal_str(k)
                if key is None:
                    continue
                if key in ("tools", "routes_to"):
                    decl[key] = _literal_list(v)
                else:
                    decl[key] = _literal_str(v) or ast.unparse(v)
        return decl


def _short(t: str) -> str:
    return t.split(".")[-1].strip()
