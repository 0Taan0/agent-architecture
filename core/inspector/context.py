"""ctx - the facade over the AST.

This layer decides whether the generic path works. The user's .architecture/inspect.py
must fit in ~50 lines for a typical hand-rolled MAS. If it needs 300, this interface is
cut wrong.

Everything here is static: no import, no execution of project code.
"""
from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from core.models import (
    AgentNode,
    CodeRef,
    Connection,
    ModelConfig,
    ProjectContext,
    Provenance,
    SchemaDef,
    SchemaField,
    UnresolvedItem,
    agent_id,
    connection_id,
    schema_id,
)

SCHEMA_BASES = {"BaseModel", "TypedDict", "pydantic.BaseModel", "typing.TypedDict",
                "typing_extensions.TypedDict"}


@dataclass
class ParsedModule:
    rel: str
    module: str
    tree: ast.Module
    source: str
    imports: dict[str, str] = field(default_factory=dict)  # local name -> dotted origin

    def __post_init__(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.imports[a.asname or a.name.split(".")[0]] = a.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    self.imports[a.asname or a.name] = f"{node.module}.{a.name}"

    def resolve(self, expr: ast.expr) -> str:
        """Best-effort dotted name for an expression like `Base`, `core.base.Base`."""
        if isinstance(expr, ast.Name):
            return self.imports.get(expr.id, f"{self.module}.{expr.id}" if self.module else expr.id)
        if isinstance(expr, ast.Attribute):
            return f"{self.resolve(expr.value)}.{expr.attr}"
        if isinstance(expr, ast.Subscript):   # Generic[T] -> Generic
            return self.resolve(expr.value)
        if isinstance(expr, ast.Call):
            return self.resolve(expr.func)
        return ast.unparse(expr)


@dataclass(frozen=True)
class ClassInfo:
    name: str
    module: ParsedModule
    node: ast.ClassDef

    @property
    def file(self) -> str:
        return self.module.rel

    @property
    def line(self) -> int:
        return self.node.lineno

    @property
    def qualname(self) -> str:
        return f"{self.module.module}.{self.name}" if self.module.module else self.name

    @property
    def bases(self) -> list[str]:
        return [self.module.resolve(b) for b in self.node.bases]

    @property
    def decorators(self) -> list[ast.expr]:
        return list(self.node.decorator_list)

    def method(self, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        for item in self.node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
                return item
        return None

    def class_attr(self, name: str) -> ast.expr | None:
        for item in self.node.body:
            if isinstance(item, ast.Assign):
                for t in item.targets:
                    if isinstance(t, ast.Name) and t.id == name:
                        return item.value
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) \
                    and item.target.id == name and item.value is not None:
                return item.value
        return None


@dataclass(frozen=True)
class CallInfo:
    file: str
    line: int
    node: ast.Call
    module: ParsedModule

    def arg_str(self, index: int) -> str | None:
        if index < len(self.node.args):
            return _literal_str(self.node.args[index])
        return None

    def kwarg(self, name: str) -> ast.expr | None:
        for kw in self.node.keywords:
            if kw.arg == name:
                return kw.value
        return None


def _literal_str(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_list(node: ast.expr | None) -> list[str]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        out = []
        for e in node.elts:
            s = _literal_str(e)
            out.append(s if s is not None else ast.unparse(e))
        return out
    return []


class InspectContext:
    """Facade handed to adapters and to the user's `discover(ctx)` function."""

    def __init__(self, project: ProjectContext, adapter: str = "project_local",
                 provenance: Provenance = Provenance.DECLARED) -> None:
        self.project = project
        self.adapter = adapter
        self.default_provenance = provenance
        self._unresolved: list[UnresolvedItem] = []

    # --- parsing ---------------------------------------------------------------
    @cached_property
    def modules(self) -> list[ParsedModule]:
        out: list[ParsedModule] = []
        for path in self.project.python_files():
            try:
                src = path.read_text(encoding="utf-8")
                tree = ast.parse(src, filename=str(path))
            except (SyntaxError, UnicodeDecodeError) as exc:
                self._unresolved.append(UnresolvedItem(
                    kind="parse_error", location=self.project.rel(path),
                    message=str(exc), source_adapter=self.adapter))
                continue
            out.append(ParsedModule(rel=self.project.rel(path),
                                    module=self.project.module_name(path), tree=tree, source=src))
        return out

    @cached_property
    def classes(self) -> list[ClassInfo]:
        return [ClassInfo(name=n.name, module=m, node=n)
                for m in self.modules for n in ast.walk(m.tree) if isinstance(n, ast.ClassDef)]

    # --- queries ---------------------------------------------------------------
    def classes_inheriting(self, base: str, *, transitive: bool = True) -> list[ClassInfo]:
        """Classes whose base resolves to `base` ("core.base.BaseAgent" or just "BaseAgent")."""
        def matches(b: str) -> bool:
            return b == base or b.endswith("." + base) or (
                "." not in base and b.split(".")[-1] == base)

        direct = [c for c in self.classes if any(matches(b) for b in c.bases)]
        if not transitive:
            return direct
        seen = {c.qualname for c in direct}
        frontier = list(direct)
        while frontier:
            parent = frontier.pop()
            for c in self.classes:
                if c.qualname in seen:
                    continue
                if any(b == parent.qualname or b.endswith("." + parent.name) for b in c.bases):
                    seen.add(c.qualname)
                    direct.append(c)
                    frontier.append(c)
        return direct

    def classes_with_decorator(self, name: str) -> list[ClassInfo]:
        out = []
        for c in self.classes:
            for d in c.decorators:
                target = d.func if isinstance(d, ast.Call) else d
                dotted = c.module.resolve(target)
                if dotted == name or dotted.split(".")[-1] == name:
                    out.append(c)
                    break
        return out

    def classes_with_attr(self, attr: str) -> list[ClassInfo]:
        return [c for c in self.classes if c.class_attr(attr) is not None]

    def classes_named(self, suffix: str) -> list[ClassInfo]:
        return [c for c in self.classes if c.name.endswith(suffix)]

    def decorator_call(self, cls: ClassInfo, name: str) -> ast.Call | None:
        for d in cls.decorators:
            if isinstance(d, ast.Call):
                dotted = cls.module.resolve(d.func)
                if dotted == name or dotted.split(".")[-1] == name:
                    return d
        return None

    def annotation_of(self, cls: ClassInfo, method: str, param: str) -> str | None:
        fn = cls.method(method)
        if fn is None:
            return None
        for a in fn.args.args + fn.args.kwonlyargs:
            if a.arg == param and a.annotation is not None:
                return ast.unparse(a.annotation)
        return None

    def first_param_annotation(self, cls: ClassInfo, method: str) -> str | None:
        fn = cls.method(method)
        if fn is None:
            return None
        params = [a for a in fn.args.args if a.arg not in ("self", "cls")]
        if params and params[0].annotation is not None:
            return ast.unparse(params[0].annotation)
        return None

    def return_annotation(self, cls: ClassInfo, method: str) -> str | None:
        fn = cls.method(method)
        if fn is None or fn.returns is None:
            return None
        return ast.unparse(fn.returns)

    def literal_list(self, cls: ClassInfo, attr: str) -> list[str]:
        return _literal_list(cls.class_attr(attr))

    def literal_value(self, cls: ClassInfo, attr: str) -> Any:
        node = cls.class_attr(attr)
        if node is None:
            return None
        try:
            return ast.literal_eval(node)
        except (ValueError, SyntaxError):
            return ast.unparse(node)

    def calls_to(self, dotted: str) -> list[CallInfo]:
        """All call sites whose callee resolves to `dotted` (suffix match allowed)."""
        out = []
        for m in self.modules:
            for n in ast.walk(m.tree):
                if isinstance(n, ast.Call):
                    callee = m.resolve(n.func)
                    if callee == dotted or callee.endswith("." + dotted):
                        out.append(CallInfo(file=m.rel, line=n.lineno, node=n, module=m))
        return out

    def function_def(self, module: ParsedModule, name: str) -> ast.FunctionDef | None:
        for n in ast.walk(module.tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
                return n
        return None

    def find_symbol(self, name: str) -> tuple[ParsedModule, ast.AST] | None:
        for m in self.modules:
            for n in ast.walk(m.tree):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                        and n.name == name:
                    return m, n
        return None

    # --- identity --------------------------------------------------------------
    def stable_id(self, cls: ClassInfo) -> str:
        return agent_id(cls.file, cls.name)

    def ref(self, cls: ClassInfo) -> CodeRef:
        return CodeRef(file=cls.file, symbol=cls.name, line=cls.line)

    # --- schemas ---------------------------------------------------------------
    @cached_property
    def schema_classes(self) -> dict[str, ClassInfo]:
        """Pydantic models, TypedDicts, dataclasses - keyed by class name."""
        out: dict[str, ClassInfo] = {}
        for c in self.classes:
            is_schema = any(b in SCHEMA_BASES or b.split(".")[-1] in {"BaseModel", "TypedDict"}
                            for b in c.bases)
            if not is_schema:
                is_schema = any(c.module.resolve(d.func if isinstance(d, ast.Call) else d)
                                .split(".")[-1] == "dataclass" for d in c.decorators)
            if is_schema:
                out.setdefault(c.name, c)
        return out

    def schema_for(self, type_expr: str | None, *,
                   provenance: Provenance = Provenance.CONFIRMED) -> SchemaDef | None:
        """Resolve a type annotation string (e.g. 'ResearchResult', 'list[Task]')
        to a SchemaDef read structurally from the code."""
        if not type_expr:
            return None
        name = _strip_generics(type_expr)
        cls = self.schema_classes.get(name)
        if cls is None:
            return None
        return self.schema(cls, provenance=provenance)

    def schema(self, cls: ClassInfo, *, provenance: Provenance = Provenance.CONFIRMED) -> SchemaDef:
        fields: list[SchemaField] = []
        for item in cls.node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                t = ast.unparse(item.annotation)
                optional = item.value is not None or t.startswith("Optional[") or "| None" in t
                fields.append(SchemaField(name=item.target.id, type=t, optional=optional))
        return SchemaDef(id=schema_id(cls.file, cls.name), name=cls.name, fields=fields,
                         code_ref=self.ref(cls), provenance=provenance,
                         source_adapter=self.adapter)

    # --- element constructors (used by discover(ctx)) ---------------------------
    def agent(self, *, id: str, name: str, input_schema: str | SchemaDef | None = None,
              output_schema: str | SchemaDef | None = None, tools: list[str] | None = None,
              code_ref: CodeRef | None = None, model: ModelConfig | dict | None = None,
              description: str | None = None,
              provenance: Provenance | None = None) -> AgentNode:
        if isinstance(model, dict):
            model = ModelConfig(**model)
        return AgentNode(
            id=id, name=name, description=description, model=model,
            input_schema=self._schema_ref(input_schema),
            output_schema=self._schema_ref(output_schema),
            tools=list(tools or []), code_ref=code_ref,
            provenance=provenance or self.default_provenance, source_adapter=self.adapter)

    def connection(self, source: str, target: str, condition: str | None = None,
                   provenance: Provenance | None = None) -> Connection:
        return Connection(id=connection_id(source, target, condition), source=source,
                          target=target, condition=condition,
                          provenance=provenance or self.default_provenance,
                          source_adapter=self.adapter)

    def unresolved(self, kind: str, location: str | None = None,
                   message: str | None = None) -> UnresolvedItem:
        return UnresolvedItem(kind=kind, location=location, message=message,
                              source_adapter=self.adapter)

    def pending_unresolved(self) -> list[UnresolvedItem]:
        _ = self.modules  # ensure parsed
        return list(self._unresolved)

    # --- internals -------------------------------------------------------------
    _discovered_schemas: dict[str, SchemaDef]

    def _schema_ref(self, value: str | SchemaDef | None) -> str | None:
        """Accept a SchemaDef, a schema id, or a type annotation string. Returns a
        SchemaDef.id and records the schema so the adapter can emit it."""
        if value is None:
            return None
        if not hasattr(self, "_discovered_schemas"):
            self._discovered_schemas = {}
        if isinstance(value, SchemaDef):
            self._discovered_schemas[value.id] = value
            return value.id
        if value.startswith("schema_"):
            return value
        sd = self.schema_for(value)
        if sd is None:
            return None   # unknown type -> not a schema we can point at (stays unresolved)
        self._discovered_schemas[sd.id] = sd
        return sd.id

    def discovered_schemas(self) -> list[SchemaDef]:
        return list(getattr(self, "_discovered_schemas", {}).values())

    def iter_classes(self) -> Iterator[ClassInfo]:
        yield from self.classes

    def path(self, rel: str) -> Path:
        return self.project.abs(rel)


def _strip_generics(t: str) -> str:
    t = t.strip()
    for wrapper in ("Optional[", "list[", "List[", "Sequence[", "Iterable[", "AsyncIterator["):
        if t.startswith(wrapper) and t.endswith("]"):
            t = t[len(wrapper):-1]
    t = t.replace("| None", "").strip()
    return t.split(".")[-1].strip()
