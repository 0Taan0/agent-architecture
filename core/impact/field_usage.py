"""Field-granular usages: where is `result.confidence` read?
Direct attribute access -> confirmed. Dict subscripts with a literal key -> inferred
(we cannot know statically which dict it is). Dynamic keys -> unresolved."""
from __future__ import annotations

import ast

from pydantic import BaseModel

from core.inspector.context import InspectContext
from core.models import ProjectContext, Provenance


class FieldUsage(BaseModel):
    file: str
    line: int
    kind: str                     # read | write | subscript | assert
    expression: str
    provenance: Provenance


def find_field_usages(project: ProjectContext, field: str,
                      ctx: InspectContext | None = None) -> list[FieldUsage]:
    ctx = ctx or InspectContext(project, adapter="impact")
    out: list[FieldUsage] = []
    for m in ctx.modules:
        for n in ast.walk(m.tree):
            if isinstance(n, ast.Attribute) and n.attr == field:
                kind = "write" if isinstance(n.ctx, ast.Store) else "read"
                out.append(FieldUsage(file=m.rel, line=n.lineno, kind=kind,
                                      expression=ast.unparse(n), provenance=Provenance.CONFIRMED))
            elif isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) \
                    and n.slice.value == field:
                out.append(FieldUsage(file=m.rel, line=n.lineno, kind="subscript",
                                      expression=ast.unparse(n), provenance=Provenance.INFERRED))
        for n in ast.walk(m.tree):
            if isinstance(n, ast.Assert) and field in ast.unparse(n.test):
                out.append(FieldUsage(file=m.rel, line=n.lineno, kind="assert",
                                      expression=ast.unparse(n.test),
                                      provenance=Provenance.CONFIRMED))
    return sorted(out, key=lambda u: (u.file, u.line))
