"""Plan phase of the Proposal/Applier pattern. Never writes.

The plan IS the Change Preview (Spec 7.8): every operation carries old and new content
and a unified diff. Ownership rules from Spec 7.1/7.2 decide the kind:

  generated file, absent                  -> create
  generated file, unchanged since gen.    -> update (or skip if identical)
  generated file, hash differs            -> conflict   (user edited generated code)
  any existing file unknown to manifest   -> conflict   (existing = user-owned)
  user file, absent                       -> create
  user file, present                      -> skip
"""
from __future__ import annotations

import difflib
from enum import Enum
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, Field

from core import PLATFORM_VERSION
from core.models import AgentNode, ArchitectureModel, ProjectContext
from core.ownership import Manifest, Ownership, architecture_hash, content_hash, file_hash

from .naming import pascal, snake

TEMPLATES = Path(__file__).parent / "templates"
TEMPLATE_VERSIONS = {"agent_base": "agent_base@1", "agent_impl": "agent_impl@1",
                     "schemas": "schemas@1", "runtime": "runtime@1",
                     "orchestrator": "orchestrator@1", "main": "main@1"}


class OperationKind(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    SKIP = "skip"
    CONFLICT = "conflict"


class FileOperation(BaseModel):
    kind: OperationKind
    path: str
    ownership: Ownership
    node_id: str | None = None
    symbol: str | None = None
    template: str | None = None
    reason: str
    old_content: str | None = None
    new_content: str | None = None
    diff: str | None = None


class Plan(BaseModel):
    architecture_hash: str
    operations: list[FileOperation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def writable(self) -> list[FileOperation]:
        return [o for o in self.operations if o.kind in (OperationKind.CREATE, OperationKind.UPDATE)]

    def conflicts(self) -> list[FileOperation]:
        return [o for o in self.operations if o.kind == OperationKind.CONFLICT]


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATES)), undefined=StrictUndefined,
                       keep_trailing_newline=True, trim_blocks=False, lstrip_blocks=False)


def _agent_ctx(model: ArchitectureModel, a: AgentNode) -> dict:
    inp = model.schema(a.input_schema) if a.input_schema else None
    out = model.schema(a.output_schema) if a.output_schema else None
    return {
        "id": a.id, "name": a.name, "snake": snake(a.name), "pascal": pascal(a.name),
        "tools": list(a.tools), "model": a.model.model_dump(exclude_none=True) if a.model else {},
        "input_type": inp.name if inp else "Any", "output_type": out.name if out else "Any",
        "routes_to": sorted(model.agent(c.target).name for c in model.connections
                            if c.source == a.id and model.agent(c.target)),
    }


def execution_order(model: ArchitectureModel) -> list[AgentNode]:
    """Topological order over connections, starting at __start__ or at sources.
    Cycles / conditionals are flattened - the orchestrator is deliberately thin."""
    ids = {a.id for a in model.agents}
    incoming = {i: 0 for i in ids}
    out: dict[str, list[str]] = {i: [] for i in ids}
    for c in model.connections:
        if c.source in ids and c.target in ids:
            out[c.source].append(c.target)
            incoming[c.target] += 1
    ready = sorted(i for i in ids if incoming[i] == 0)
    order: list[str] = []
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        for nxt in sorted(out[cur]):
            incoming[nxt] -= 1
            if incoming[nxt] == 0:
                ready.append(nxt)
    order += sorted(i for i in ids if i not in order)   # cycle members, keep them anyway
    return [model.agent(i) for i in order]


def plan_generation(project: ProjectContext, model: ArchitectureModel,
                    manifest: Manifest) -> Plan:
    env = _env()
    base = {"platform_version": PLATFORM_VERSION}
    files: list[tuple[str, str, Ownership, str | None, str | None, str]] = []  # path, content, own, node, symbol, tpl

    ordered = execution_order(model)
    agent_ctxs = [_agent_ctx(model, a) for a in ordered]

    files.append(("orchestration/_generated/__init__.py", "", Ownership.GENERATED, None, None, "pkg"))
    files.append(("orchestration/_generated/runtime.py",
                  env.get_template("runtime.py.j2").render(**base, template=TEMPLATE_VERSIONS["runtime"]),
                  Ownership.GENERATED, None, "AgentBase", TEMPLATE_VERSIONS["runtime"]))
    files.append(("models/_generated/__init__.py", "", Ownership.GENERATED, None, None, "pkg"))
    files.append(("models/_generated/schemas.py",
                  env.get_template("schemas.py.j2").render(**base, schemas=model.schemas,
                                                           template=TEMPLATE_VERSIONS["schemas"]),
                  Ownership.GENERATED, None, None, TEMPLATE_VERSIONS["schemas"]))
    files.append(("agents/__init__.py", "", Ownership.USER, None, None, "pkg"))
    files.append(("agents/_generated/__init__.py", "", Ownership.GENERATED, None, None, "pkg"))
    for a, ctx in zip(ordered, agent_ctxs):
        base_tpl, impl_tpl = TEMPLATE_VERSIONS["agent_base"], TEMPLATE_VERSIONS["agent_impl"]
        files.append((f"agents/_generated/{ctx['snake']}_base.py",
                      env.get_template("agent_base.py.j2").render(**base, agent=ctx, template=base_tpl),
                      Ownership.GENERATED, a.id, f"{ctx['pascal']}AgentBase", TEMPLATE_VERSIONS["agent_base"]))
        files.append((f"agents/{ctx['snake']}.py",
                      env.get_template("agent_impl.py.j2").render(**base, agent=ctx, template=impl_tpl),
                      Ownership.USER, a.id, f"{ctx['pascal']}Agent", TEMPLATE_VERSIONS["agent_impl"]))
    files.append(("orchestration/__init__.py", "", Ownership.USER, None, None, "pkg"))
    files.append(("orchestration/_generated/orchestrator.py",
                  env.get_template("orchestrator.py.j2").render(
                      **base, agents=agent_ctxs, order=[c["name"] for c in agent_ctxs],
                      template=TEMPLATE_VERSIONS["orchestrator"]),
                  Ownership.GENERATED, None, "run_pipeline", TEMPLATE_VERSIONS["orchestrator"]))
    files.append(("models/__init__.py", "", Ownership.USER, None, None, "pkg"))
    files.append(("main.py", env.get_template("main.py.j2").render(**base, template=TEMPLATE_VERSIONS["main"]),
                  Ownership.USER, None, "main", TEMPLATE_VERSIONS["main"]))

    plan = Plan(architecture_hash=architecture_hash(model))
    for rel, content, own, node_id, symbol, tpl in files:
        plan.operations.append(_classify(project, manifest, rel, content, own, node_id, symbol, tpl))
    if not model.agents:
        plan.warnings.append("architecture has no agents - nothing meaningful to generate")
    return plan


def _classify(project: ProjectContext, manifest: Manifest, rel: str, content: str,
              own: Ownership, node_id: str | None, symbol: str | None, tpl: str) -> FileOperation:
    path = project.abs(rel)
    entry = manifest.files.get(rel)
    common = {"path": rel, "ownership": own, "node_id": node_id, "symbol": symbol,
              "template": tpl, "new_content": content}
    if not path.exists():
        return FileOperation(kind=OperationKind.CREATE, reason="file does not exist", **common)
    old = path.read_text(encoding="utf-8")
    common["old_content"] = old
    if own == Ownership.USER:
        return FileOperation(kind=OperationKind.SKIP, reason="user-owned file exists",
                             **common)
    if entry is None:
        return FileOperation(kind=OperationKind.CONFLICT,
                             reason="file exists but is not in the manifest - treated as "
                                    "user-owned", **common)
    current = file_hash(path)
    if entry.content_hash != current:
        return FileOperation(kind=OperationKind.CONFLICT,
                             reason="generated file was edited by hand (hash mismatch)",
                             diff=_diff(rel, old, content), **common)
    if content_hash(content) == current:
        return FileOperation(kind=OperationKind.SKIP, reason="identical", **common)
    return FileOperation(kind=OperationKind.UPDATE, reason="template output changed",
                         diff=_diff(rel, old, content), **common)


def _diff(rel: str, old: str, new: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                        fromfile=f"a/{rel}", tofile=f"b/{rel}"))
