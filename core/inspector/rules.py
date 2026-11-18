"""Mapping rules: `.architecture/mapping.yaml` describes how THIS project's code maps to
agents and connections. The discovery agent proposes the rules, the user accepts them,
and from then on they are applied here, statically and without any LLM.

Provenance: an accepted rule yields `declared` elements, a proposed rule `inferred`
ones, a rejected rule nothing. A rule that matches nothing is reported as `rule_stale`.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from core.inspector.base import Confidence, ExtractionResult
from core.inspector.context import CallInfo, ClassInfo, InspectContext, _literal_str
from core.models import CodeRef, ModelConfig, ProjectContext, Provenance, UnresolvedItem, agent_id

MAPPING_FILE = ".architecture/mapping.yaml"
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


# Rule model
class _Strict(BaseModel):
    # Unknown keys are errors, so a typo in mapping.yaml never passes silently
    model_config = ConfigDict(extra="forbid")

    def _require_one(self, *names: str) -> None:
        given = [n for n in names if getattr(self, n)]
        if len(given) != 1:
            raise ValueError(f"exactly one of {', '.join(names)} is required, got {given or 'none'}")


class Status(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class Source(_Strict):
    """Where a value is read from. Exactly one field is set."""
    attr: str | None = None          # class attribute, e.g. TOOLS
    param_of: str | None = None      # annotation of the first parameter of this method
    returns_of: str | None = None    # return annotation of this method
    arg: int | None = None           # positional argument of the matched call
    kwarg: str | None = None         # keyword argument of the matched call
    assigned: bool = False           # name the result of the matched call is assigned to
    enclosing: bool = False          # agent class that contains the matched call

    @model_validator(mode="after")
    def _one_field(self) -> Source:
        given = [n for n in type(self).model_fields
                 if getattr(self, n) is not None and getattr(self, n) is not False]
        if len(given) != 1:
            raise ValueError(f"a source needs exactly one field, got {given or 'none'}")
        return self


class Rule(_Strict):
    id: str
    status: Status = Status.PROPOSED
    evidence: list[str] = Field(default_factory=list)   # "file:line"
    rationale: str | None = None


class AgentRule(Rule):
    # Selector, exactly one. The first five select classes, `calls` selects call sites.
    inherits: str | None = None
    decorator: str | None = None
    has_attr: str | None = None
    name_suffix: str | None = None
    classes: list[str] = Field(default_factory=list)
    calls: str | None = None
    exclude: list[str] = Field(default_factory=list)    # class names to skip, e.g. bases
    # Where the agent's properties come from
    name: Source | None = None
    input_schema: Source | None = None
    output_schema: Source | None = None
    tools: Source | None = None
    model: Source | None = None
    impl: Source | None = None                          # call rules: the implementing symbol

    @model_validator(mode="after")
    def _one_selector(self) -> AgentRule:
        self._require_one("inherits", "decorator", "has_attr", "name_suffix", "classes", "calls")
        return self


class ConnectionRule(Rule):
    # Kind, exactly one
    sequence: str | None = None      # module-level list: consecutive items are connected
    calls: str | None = None         # every matched call yields edges
    edges: list[tuple[str, str]] = Field(default_factory=list)   # explicit [source, target]
    # For `calls`: either source and target, or chain (a list argument, connected in order)
    source: Source | None = None
    target: Source | None = None
    chain: Source | None = None
    ignore: list[str] = Field(default_factory=list)     # endpoints that are not agents

    @model_validator(mode="after")
    def _one_kind(self) -> ConnectionRule:
        self._require_one("sequence", "calls", "edges")
        if self.calls and bool(self.chain) == bool(self.source and self.target):
            raise ValueError("a `calls` connection needs either source and target, or chain")
        return self


class UnresolvedRule(Rule):
    """Marks call sites that cannot be read statically, so they show up instead of vanishing."""
    calls: str
    kind: str
    message: str | None = None


class Meta(_Strict):
    digest_hash: str | None = None
    model: str | None = None
    prompt_version: int | None = None


class Mapping(_Strict):
    version: int = 1
    meta: Meta | None = None
    agents: list[AgentRule] = Field(default_factory=list)
    connections: list[ConnectionRule] = Field(default_factory=list)
    unresolved: list[UnresolvedRule] = Field(default_factory=list)
    descriptions: dict[str, str] = Field(default_factory=dict)   # agent name -> plain text

    def rules(self) -> list[Rule]:
        return [*self.agents, *self.connections, *self.unresolved]


# Reading and writing mapping.yaml
def parse_mapping(text: str) -> Mapping:
    return Mapping.model_validate(yaml.safe_load(text) or {})


def dump_mapping(mapping: Mapping) -> str:
    data = _pruned(mapping.model_dump(mode="json"))
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def rule_text(rule: Rule) -> str:
    """What a rule says on one line, without its id and the fields that only document it."""
    data = rule.model_dump(mode="json", exclude={"id", "status", "evidence", "rationale"})
    return yaml.safe_dump(_pruned(data), default_flow_style=True, sort_keys=False,
                          width=10_000).strip()[1:-1]


def load_mapping(root: Path) -> Mapping | None:
    path = Path(root) / MAPPING_FILE
    return parse_mapping(path.read_text(encoding="utf-8")) if path.exists() else None


def save_mapping(root: Path, mapping: Mapping) -> Path:
    path = Path(root) / MAPPING_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_mapping(mapping), encoding="utf-8")
    return path


def _pruned(value):
    # Drop unset fields so the file only shows what a rule actually says
    if isinstance(value, dict):
        return {k: _pruned(v) for k, v in value.items()
                if not (v is None or v is False or v == [] or v == {})}
    if isinstance(value, list):
        return [_pruned(v) for v in value]
    return value


# Applying the rules
@dataclass
class Applied:
    result: ExtractionResult
    matches: dict[str, int]                  # rule id -> number of elements it produced
    files: dict[str, set[str]]               # rule id -> files in which it matched
    elements: dict[str, list[str]]           # rule id -> ids of the agents and connections it yields
    problems: list[tuple[str, str, str]] = field(default_factory=list)   # (rule id, kind, message)


@dataclass
class _Target:
    # What a rule matched: a class, or a call plus the symbol that implements the agent
    cls: ClassInfo | None = None
    call: CallInfo | None = None
    fn: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    fn_file: str | None = None

    @property
    def location(self) -> str:
        return f"{self.call.file}:{self.call.line}" if self.call else f"{self.cls.file}:{self.cls.line}"


def apply_mapping(ctx: InspectContext, mapping: Mapping) -> Applied:
    return _Run(ctx, mapping).run()


class _Run:
    def __init__(self, ctx: InspectContext, mapping: Mapping) -> None:
        self.ctx = ctx
        self.mapping = mapping
        self.result = ExtractionResult()
        self.matches = {r.id: 0 for r in mapping.rules() if r.status != Status.REJECTED}
        self.files: dict[str, set[str]] = {rule_id: set() for rule_id in self.matches}
        self.elements: dict[str, list[str]] = {rule_id: [] for rule_id in self.matches}
        self.problems: list[tuple[str, str, str]] = []
        self.agent_file: dict[str, str] = {}
        # Names an edge may use for an agent -> agent id. None marks an ambiguous name.
        self.aliases: dict[str, str | None] = {}
        self.agent_of_class: dict[str, str] = {}

    def run(self) -> Applied:
        for rule in self._active(self.mapping.agents):
            targets = self._class_targets(rule) if not rule.calls else self._call_targets(rule)
            for target in targets:
                self._agent(rule, target)
        for rule in self._active(self.mapping.connections):
            self._connections(rule)
        for rule in self._active(self.mapping.unresolved):
            for call in self.ctx.calls_matching(rule.calls):
                self._match(rule, call.file)
                self.result.unresolved.append(self.ctx.unresolved(
                    rule.kind, f"{call.file}:{call.line}", rule.message))
        for rule_id, count in self.matches.items():
            if count == 0:
                self._problem(rule_id, "rule_stale", MAPPING_FILE, "matches nothing in the code")
        self.result.schemas = self.ctx.discovered_schemas()
        self.result.unresolved.extend(self.ctx.pending_unresolved())
        return Applied(self.result, self.matches, self.files, self.elements, self.problems)

    @staticmethod
    def _active(rules: list) -> list:
        return [r for r in rules if r.status != Status.REJECTED]

    @staticmethod
    def _provenance(rule: Rule) -> Provenance:
        return Provenance.DECLARED if rule.status == Status.ACCEPTED else Provenance.INFERRED

    def _match(self, rule: Rule, *files: str | None, element: str | None = None) -> None:
        self.matches[rule.id] += 1
        self.files[rule.id].update(f for f in files if f and f != MAPPING_FILE)
        if element and element not in self.elements[rule.id]:
            self.elements[rule.id].append(element)

    def _problem(self, rule_id: str, kind: str, location: str, message: str) -> None:
        self.problems.append((rule_id, kind, message))
        self.result.unresolved.append(self.ctx.unresolved(kind, location, f"rule {rule_id}: {message}"))

    # Agents
    def _class_targets(self, rule: AgentRule) -> list[_Target]:
        if rule.inherits:
            found = self.ctx.classes_inheriting(rule.inherits)
        elif rule.decorator:
            found = self.ctx.classes_with_decorator(rule.decorator)
        elif rule.has_attr:
            found = self.ctx.classes_with_attr(rule.has_attr)
        elif rule.name_suffix:
            found = self.ctx.classes_named(rule.name_suffix)
        else:
            found = [c for c in self.ctx.classes if c.name in rule.classes]
        found = [c for c in found if c.name not in rule.exclude]
        return [_Target(cls=c) for c in sorted(found, key=lambda c: (c.file, c.line))]

    def _call_targets(self, rule: AgentRule) -> list[_Target]:
        targets = []
        for call in self.ctx.calls_matching(rule.calls):
            target = _Target(call=call)
            symbol = self._symbol(call, self._node(rule.impl, target))
            if isinstance(symbol, ClassInfo):
                target.cls = symbol
            elif symbol is not None:
                target.fn_file, target.fn = symbol
            targets.append(target)
        return targets

    def _agent(self, rule: AgentRule, target: _Target) -> None:
        name = self._text(rule.name, target) if rule.name else self._default_name(target)
        if not name:
            self._problem(rule.id, "not_static", target.location, "the agent name is not readable statically")
            return
        if target.call is None:
            ident, ref = self.ctx.stable_id(target.cls), self.ctx.ref(target.cls)
            self.agent_of_class[target.cls.qualname] = ident
        else:
            ident = agent_id(target.call.file, name)
            ref = self._impl_ref(target) or CodeRef(file=target.call.file, line=target.call.line)
        if any(a.id == ident for a in self.result.agents):
            return
        model = self._literal(rule, rule.model, target, "model")
        agent = self.ctx.agent(
            id=ident, name=name, code_ref=ref,
            input_schema=self._annotation(rule.input_schema, target),
            output_schema=self._annotation(rule.output_schema, target),
            tools=self._tools(rule, target),
            model=ModelConfig(model=model) if model else None,
            description=self.mapping.descriptions.get(name),
            provenance=self._provenance(rule))
        self.result.agents.append(agent)
        self.agent_file[ident] = ref.file
        self._match(rule, ref.file, target.call.file if target.call else None, element=ident)
        for alias in {name, ref.symbol, self._assigned(target.call) if target.call else None}:
            if alias:
                self.aliases[alias] = ident if self.aliases.get(alias, ident) == ident else None

    def _default_name(self, target: _Target) -> str | None:
        return target.cls.name if target.call is None else self._assigned(target.call)

    def _impl_ref(self, target: _Target) -> CodeRef | None:
        if target.cls is not None:
            return self.ctx.ref(target.cls)
        if target.fn is not None:
            return CodeRef(file=target.fn_file, symbol=target.fn.name, line=target.fn.lineno)
        return None

    def _tools(self, rule: AgentRule, target: _Target) -> list[str]:
        node = self._node(rule.tools, target)
        if node is None:
            return []
        if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            self._problem(rule.id, "not_static", target.location, "tools are not a literal list")
            return []
        return [_literal_str(e) or ast.unparse(e) for e in node.elts]

    def _literal(self, rule: Rule, source: Source | None, target: _Target, what: str) -> str | None:
        node = self._node(source, target)
        if node is None:
            return None
        value = _literal_str(node)
        if value is None:
            self._problem(rule.id, "not_static", target.location, f"{what} is not a string literal")
        return value

    # Reading values
    def _node(self, source: Source | None, target: _Target) -> ast.expr | None:
        if source is None:
            return None
        if source.attr is not None and target.cls is not None:
            return target.cls.class_attr(source.attr)
        if target.call is not None:
            if source.arg is not None and source.arg < len(target.call.node.args):
                return target.call.node.args[source.arg]
            if source.kwarg is not None:
                return target.call.kwarg(source.kwarg)
        return None

    def _text(self, source: Source, target: _Target) -> str | None:
        # A string literal, or the bare name of a referenced symbol
        if source.assigned:
            return self._assigned(target.call) if target.call else None
        return _name_of(self._node(source, target))

    def _annotation(self, source: Source | None, target: _Target) -> str | None:
        method = source and (source.param_of or source.returns_of)
        if not method:
            return None
        # A function is its own __call__, so `param_of: __call__` reads a function's signature
        fn = target.cls.method(method) if target.cls else (target.fn if method == "__call__" else None)
        if fn is None:
            return None
        if source.returns_of:
            return ast.unparse(fn.returns) if fn.returns else None
        params = [a for a in fn.args.posonlyargs + fn.args.args if a.arg not in ("self", "cls")]
        return ast.unparse(params[0].annotation) if params and params[0].annotation else None

    def _symbol(self, call: CallInfo, node: ast.expr | None):
        # Class or function a call argument refers to: ClassInfo, (file, function node) or None
        if node is None or isinstance(node, ast.Constant):
            return None
        dotted = call.module.resolve(node)
        for cls in self.ctx.classes:
            if cls.qualname == dotted:
                return cls
        for m in self.ctx.modules:
            for n in m.tree.body:
                if isinstance(n, _FUNCS) and f"{m.module}.{n.name}" == dotted:
                    return m.rel, n
        return None

    def _assigned(self, call: CallInfo) -> str | None:
        if not hasattr(self, "_assignments"):
            self._assignments: dict[int, str] = {}
            for m in self.ctx.modules:
                for n in ast.walk(m.tree):
                    if isinstance(n, ast.Assign) and len(n.targets) == 1:
                        name = _name_of(n.targets[0])
                        if name:
                            self._assignments[id(n.value)] = name
        return self._assignments.get(id(call.node))

    def _enclosing_agent(self, call: CallInfo) -> str | None:
        if not hasattr(self, "_owners"):
            self._owners: dict[int, ClassInfo] = {}
            # Outer classes come first, so an inner class overrides its outer one
            for cls in self.ctx.classes:
                for n in ast.walk(cls.node):
                    if isinstance(n, ast.Call):
                        self._owners[id(n)] = cls
        owner = self._owners.get(id(call.node))
        return self.agent_of_class.get(owner.qualname) if owner else None

    # Connections
    def _connections(self, rule: ConnectionRule) -> None:
        if rule.sequence:
            hit = self._module_list(rule.sequence)
            if hit:
                self._chain(rule, [_name_of(e) for e in hit[1].elts], hit[0])
        elif rule.edges:
            for source, target in rule.edges:
                self._edge(rule, source, target, None, MAPPING_FILE)
        else:
            for call in self.ctx.calls_matching(rule.calls):
                where, target = f"{call.file}:{call.line}", _Target(call=call)
                if rule.chain:
                    node = self._node(rule.chain, target)
                    if isinstance(node, (ast.List, ast.Tuple)):
                        self._chain(rule, [_name_of(e) for e in node.elts], where)
                    continue
                for source, _ in self._ends(rule.source, call):
                    for end, condition in self._ends(rule.target, call):
                        self._edge(rule, source, end, condition, where)

    def _ends(self, source: Source, call: CallInfo) -> list[tuple[str | None, str | None]]:
        # One endpoint, or several when the argument is a list or a dict (keys become conditions)
        if source.enclosing:
            return [(self._enclosing_agent(call), None)]
        node = self._node(source, _Target(call=call))
        if isinstance(node, ast.Dict):
            return [(_name_of(v), _name_of(k)) for k, v in zip(node.keys, node.values)]
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return [(_name_of(e), None) for e in node.elts]
        return [(_name_of(node), None)]

    def _chain(self, rule: ConnectionRule, names: list[str | None], where: str) -> None:
        names = [n for n in names if n not in rule.ignore]
        for source, target in zip(names, names[1:]):
            self._edge(rule, source, target, None, where)

    def _edge(self, rule: ConnectionRule, source: str | None, target: str | None,
              condition: str | None, where: str) -> None:
        if source in rule.ignore or target in rule.ignore:
            return
        ids = []
        for end in (source, target):
            # An endpoint is an agent id already, or a name that has to resolve to one agent
            ident = end if end in self.agent_of_class.values() else self.aliases.get(end or "")
            if ident is None:
                self._problem(rule.id, "unresolved_connection_target", where,
                              f"{end!r} is not a known agent" if end else "an endpoint is not readable statically")
                return
            ids.append(ident)
        connection = self.ctx.connection(ids[0], ids[1], condition, self._provenance(rule))
        if all(c.id != connection.id for c in self.result.connections):
            self.result.connections.append(connection)
        self._match(rule, where.rpartition(":")[0], self.agent_file[ids[0]], self.agent_file[ids[1]],
                    element=connection.id)

    def _module_list(self, dotted: str) -> tuple[str, ast.List | ast.Tuple] | None:
        # "pipeline.PIPELINE" -> the list literal assigned to PIPELINE in module pipeline
        module, _, name = dotted.rpartition(".")
        for m in self.ctx.modules:
            if m.module != module:
                continue
            for n in m.tree.body:
                value = n.value if isinstance(n, (ast.Assign, ast.AnnAssign)) else None
                targets = n.targets if isinstance(n, ast.Assign) else [getattr(n, "target", None)]
                if isinstance(value, (ast.List, ast.Tuple)) and any(_name_of(t) == name for t in targets):
                    return f"{m.rel}:{n.lineno}", value
        return None


def _name_of(node: ast.AST | None) -> str | None:
    # "x" -> x, x -> x, a.b.x -> x. Anything else has no static name.
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else None


# Adapter
class RulesAdapter:
    name = "rules"
    supported_versions = None

    def detect(self, project: ProjectContext) -> Confidence:
        return Confidence.STRONG if (project.root / MAPPING_FILE).exists() else Confidence.NONE

    def extract(self, project: ProjectContext) -> ExtractionResult:
        try:
            mapping = load_mapping(project.root)
        except (yaml.YAMLError, ValidationError) as exc:
            return ExtractionResult(unresolved=[UnresolvedItem(
                kind="mapping_invalid", location=MAPPING_FILE, message=str(exc),
                source_adapter=self.name)])
        return apply_mapping(InspectContext(project, adapter=self.name), mapping).result
