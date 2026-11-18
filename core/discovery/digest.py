"""Fact digest: what the discovery agent reads instead of the whole code base.

Purely static (AST through ctx), deterministic, compact: per module the imports,
module-level assignments, classes (bases, decorators, attributes, method signatures),
top-level functions and the calls that leave the module's own locals.

"Incomplete yes, wrong no": a call is only listed when its callee can be named for
certain (see core/inspector/calls.py). Rules in mapping.yaml match the same callee names.

`Digest.hash` ignores line numbers, so moving code around does not trigger a new
discovery run; changing a signature, base, attribute or call does.
"""
from __future__ import annotations

import ast
import sys

from pydantic import BaseModel, Field

from core.inspector.context import ClassInfo, InspectContext, ParsedModule
from core.models import ProjectContext
from core.ownership.hashing import canonical_json, content_hash

DIGEST_VERSION = 1
MAX_VALUE = 120      # characters kept of an unparsed value / docstring line
MAX_LINES = 5        # call sites kept per callee and module (evidence, not a census)

_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


class FunctionFact(BaseModel):
    name: str
    line: int
    params: list[str] = Field(default_factory=list)      # "task: ProcessDescription"
    returns: str | None = None
    decorators: list[str] = Field(default_factory=list)


class ClassFact(BaseModel):
    name: str
    line: int
    bases: list[str] = Field(default_factory=list)        # resolved dotted names
    decorators: list[str] = Field(default_factory=list)
    doc: str | None = None
    attrs: list[str] = Field(default_factory=list)        # class-level assignments, fields
    methods: list[FunctionFact] = Field(default_factory=list)


class CallFact(BaseModel):
    callee: str
    lines: list[int]


class ModuleFact(BaseModel):
    file: str
    module: str
    doc: str | None = None
    imports: list[str] = Field(default_factory=list)      # dotted origins, stdlib left out
    assigns: list[str] = Field(default_factory=list)      # module-level assignments
    classes: list[ClassFact] = Field(default_factory=list)
    functions: list[FunctionFact] = Field(default_factory=list)
    calls: list[CallFact] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.imports or self.assigns or self.classes or self.functions or self.calls)


class Digest(BaseModel):
    version: int = DIGEST_VERSION
    project: str
    modules: list[ModuleFact] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)       # test files, left out on purpose
    parse_errors: list[str] = Field(default_factory=list)
    hash: str = ""

    def render(self) -> str:
        """Compact text for the agent. `#L` = line number."""
        out = [f"# {self.project}"]
        for m in self.modules:
            out.append(f"\n## {m.file}" + (f"  -- {m.doc}" if m.doc else ""))
            if m.imports:
                out.append("imports: " + ", ".join(_grouped(m.imports)))
            out += m.assigns + [_signature(f) for f in m.functions]
            for c in m.classes:
                out += [f"@{d}" for d in c.decorators]
                bases = f"({', '.join(c.bases)})" if c.bases else ""
                out.append(f"class {c.name}{bases} #L{c.line}" + (f"  -- {c.doc}" if c.doc else ""))
                out += [f"  {line}" for line in c.attrs + [_signature(f) for f in c.methods]]
            if m.calls:
                out.append("calls: " + ", ".join(
                    f"{c.callee} #L{','.join(map(str, c.lines))}" for c in m.calls))
        if self.skipped:
            out.append(f"\nskipped (tests): {', '.join(self.skipped)}")
        if self.parse_errors:
            out.append(f"\nparse errors: {', '.join(self.parse_errors)}")
        return "\n".join(out) + "\n"


def _signature(f: FunctionFact) -> str:
    decorators = "".join(f"@{d} " for d in f.decorators)
    returns = f" -> {f.returns}" if f.returns else ""
    return f"{decorators}def {f.name}({', '.join(f.params)}){returns} #L{f.line}"


def _grouped(imports: list[str]) -> list[str]:
    """['a.b.X', 'a.b.Y', 'c'] -> ['a.b.{X,Y}', 'c']"""
    groups: dict[str, list[str]] = {}
    for dotted in imports:
        parent, _, name = dotted.rpartition(".")
        groups.setdefault(parent, []).append(name)
    return [(f"{parent}." if parent else "") + (names[0] if len(names) == 1 else "{" + ",".join(names) + "}")
            for parent, names in groups.items()]


def build_digest(project: ProjectContext, *, include_tests: bool = False,
                 ctx: InspectContext | None = None) -> Digest:
    """Pass an existing `ctx` to reuse its parsed modules."""
    ctx = ctx or InspectContext(project, adapter="digest")
    roots = {m.module.split(".")[0] for m in ctx.modules if m.module}
    classes: dict[str, list[ClassInfo]] = {}
    for c in ctx.classes:
        classes.setdefault(c.file, []).append(c)

    calls = _calls(ctx, roots)

    digest = Digest(project=project.display_name)
    for m in ctx.modules:
        if not include_tests and _is_test(m.rel):
            digest.skipped.append(m.rel)
            continue
        fact = _module_fact(m, classes.get(m.rel, []), calls.get(m.rel, []), roots)
        if not fact.is_empty():
            digest.modules.append(fact)
    digest.parse_errors = sorted(u.location for u in ctx.pending_unresolved()
                                 if u.kind == "parse_error" and u.location)
    digest.hash = _hash(digest)
    return digest


# Facts
def _module_fact(m: ParsedModule, classes: list[ClassInfo], calls: list[CallFact],
                 roots: set[str]) -> ModuleFact:
    return ModuleFact(
        file=m.rel, module=m.module, doc=_doc(m.tree),
        imports=sorted({o for o in m.imports.values() if not _is_noise(o, roots)}),
        assigns=_assigns(m.tree.body),
        classes=[_class_fact(c) for c in sorted(classes, key=lambda c: c.line)],
        functions=[_function_fact(n) for n in m.tree.body if isinstance(n, _FUNCS)],
        calls=calls)


def _class_fact(c: ClassInfo) -> ClassFact:
    return ClassFact(
        name=c.name, line=c.line, bases=c.bases, decorators=[_short(d) for d in c.decorators],
        doc=_doc(c.node), attrs=_assigns(c.node.body),
        methods=[_function_fact(n) for n in c.node.body if isinstance(n, _FUNCS)])


def _function_fact(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> FunctionFact:
    a = fn.args
    params = [_param(p) for p in a.posonlyargs + a.args if p.arg not in ("self", "cls")]
    if a.vararg:
        params.append("*" + _param(a.vararg))
    params += [_param(p) for p in a.kwonlyargs]
    if a.kwarg:
        params.append("**" + _param(a.kwarg))
    return FunctionFact(name=fn.name, line=fn.lineno, params=params,
                        returns=_short(fn.returns) if fn.returns else None,
                        decorators=[_short(d) for d in fn.decorator_list])


def _param(p: ast.arg) -> str:
    return f"{p.arg}: {_short(p.annotation)}" if p.annotation else p.arg


def _assigns(body: list[ast.stmt]) -> list[str]:
    """Assignment statements of a module or class body: `TOOLS = ['a']`, `name: str`."""
    out = []
    for item in body:
        targets = item.targets if isinstance(item, ast.Assign) else (
            [item.target] if isinstance(item, ast.AnnAssign) else [])
        if targets and not all(isinstance(t, ast.Name) and t.id.startswith("__") for t in targets):
            out.append(_short(item))
    return out


def _doc(node: ast.AST) -> str | None:
    doc = ast.get_docstring(node)
    return _clip(doc.strip().splitlines()[0]) if doc and doc.strip() else None


def _short(node: ast.AST) -> str:
    return _clip(" ".join(ast.unparse(node).split()))


def _clip(text: str) -> str:
    return text if len(text) <= MAX_VALUE else text[:MAX_VALUE] + "…"


def _is_test(rel: str) -> bool:
    *dirs, name = rel.split("/")
    return (any(d in ("tests", "test") for d in dirs) or name.startswith("test_")
            or name.endswith("_test.py") or name == "conftest.py")


def _is_noise(dotted: str, roots: set[str]) -> bool:
    """Standard library (unless a project package shadows the name) and __future__."""
    root = dotted.split(".")[0]
    return root not in roots and root in sys.stdlib_module_names


# Calls
def _calls(ctx: InspectContext, roots: set[str]) -> dict[str, list[CallFact]]:
    # file -> callees with their call sites, standard library left out
    found: dict[str, dict[str, list[int]]] = {}
    for call in ctx.certain_calls:
        if not _is_noise(call.callee, roots):
            found.setdefault(call.file, {}).setdefault(call.callee, []).append(call.line)
    return {file: [CallFact(callee=c, lines=sorted(set(lines))[:MAX_LINES])
                   for c, lines in sorted(callees.items())]
            for file, callees in found.items()}


# Hash
def _hash(digest: Digest) -> str:
    data = digest.model_dump(mode="json", include={"version", "modules", "parse_errors"})
    return content_hash(canonical_json(_without_lines(data)))


def _without_lines(value):
    if isinstance(value, dict):
        return {k: _without_lines(v) for k, v in value.items() if k not in ("line", "lines")}
    if isinstance(value, list):
        return [_without_lines(v) for v in value]
    return value
