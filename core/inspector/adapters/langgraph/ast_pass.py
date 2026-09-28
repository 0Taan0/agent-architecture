"""AST pass over the same files: file/line for each node, state class fields,
`bind_tools(...)`, model configuration. Static, so parts of it are `inferred`.

Also provides an AST-only fallback topology (add_node/add_edge with literal names) for
the case where the subprocess import fails - marked `inferred`, never `confirmed`.
"""
from __future__ import annotations

import ast

from core.inspector.base import ExtractionResult
from core.inspector.context import CallInfo, InspectContext, _literal_list, _literal_str
from core.models import (
    AgentNode,
    CodeRef,
    Connection,
    ModelConfig,
    Provenance,
    UnresolvedItem,
    connection_id,
    stable_id,
)

ADAPTER = "langgraph"
MODEL_CLASSES = {"ChatOpenAI": "openai", "ChatAnthropic": "anthropic", "ChatGoogleGenerativeAI":
                 "google", "ChatOllama": "ollama", "AzureChatOpenAI": "azure", "ChatMistralAI":
                 "mistral", "ChatGroq": "groq", "init_chat_model": None}


def graph_modules(ctx: InspectContext) -> list[str]:
    """Modules that construct a StateGraph - candidates for subprocess introspection."""
    mods = []
    for call in ctx.calls_to("StateGraph"):
        if call.module.module and call.module.module not in mods:
            mods.append(call.module.module)
    return mods


def node_bindings(ctx: InspectContext) -> dict[str, tuple[str, str | None]]:
    """graph.add_node("name", fn) -> {"name": (module_rel, "fn")}"""
    out: dict[str, tuple[str, str | None]] = {}
    for call in ctx.calls_to("add_node"):
        name = call.arg_str(0)
        fn_expr = call.node.args[1] if len(call.node.args) > 1 else call.kwarg("action")
        if name is None and len(call.node.args) == 1:
            fn_expr = call.node.args[0]
            name = ast.unparse(fn_expr).split(".")[-1]
        if name is None:
            continue
        symbol = ast.unparse(fn_expr).split(".")[-1] if fn_expr is not None else None
        if symbol and symbol.endswith(")"):
            symbol = symbol.split("(")[0]
        out[name] = (call.file, symbol)
    return out


def enrich(ctx: InspectContext, result: ExtractionResult, name_to_id: dict[str, str]) -> None:
    """Add code_ref, tools and model config to confirmed nodes (in place)."""
    bindings = node_bindings(ctx)
    tools_by_symbol = _tools_by_symbol(ctx)
    models_by_symbol = _models_by_symbol(ctx)
    for agent in result.agents:
        binding = bindings.get(agent.name)
        if binding is None:
            continue
        file, symbol = binding
        loc = ctx.find_symbol(symbol) if symbol else None
        if loc is not None:
            module, node = loc
            agent.code_ref = CodeRef(file=module.rel, symbol=symbol, line=getattr(node, "lineno", None))
        else:
            agent.code_ref = CodeRef(file=file, symbol=symbol)
        if symbol in tools_by_symbol:
            agent.tools = tools_by_symbol[symbol]
        if symbol in models_by_symbol:
            agent.model = models_by_symbol[symbol]
    # state schema -> SchemaDef
    for call in ctx.calls_to("StateGraph"):
        state_expr = call.node.args[0] if call.node.args else call.kwarg("state_schema")
        if state_expr is not None:
            sd = ctx.schema_for(ast.unparse(state_expr))
            if sd is not None and not any(s.id == sd.id for s in result.schemas):
                sd.source_adapter = ADAPTER
                result.schemas.append(sd)
    # tool bindings that could not be attributed -> unresolved, not dropped
    for call in ctx.calls_to("bind_tools"):
        if not _literal_list(call.node.args[0] if call.node.args else None):
            result.unresolved.append(UnresolvedItem(
                kind="dynamic_tool_binding", location=f"{call.file}:{call.line}",
                message="bind_tools() argument is not a literal list", source_adapter=ADAPTER))


def fallback_topology(ctx: InspectContext) -> ExtractionResult:
    """AST-only: add_node / add_edge / add_conditional_edges with literal names. inferred."""
    result = ExtractionResult(notes=["langgraph: subprocess introspection unavailable, "
                                    "topology inferred from AST"])
    name_to_id: dict[str, str] = {}
    for name, (file, symbol) in node_bindings(ctx).items():
        aid = stable_id("agent", file, f"node:{name}")
        name_to_id[name] = aid
        loc = ctx.find_symbol(symbol) if symbol else None
        ref = CodeRef(file=loc[0].rel, symbol=symbol, line=loc[1].lineno) if loc else \
            CodeRef(file=file, symbol=symbol)
        result.agents.append(AgentNode(id=aid, name=name, code_ref=ref,
                                       provenance=Provenance.INFERRED, source_adapter=ADAPTER))
    for call in ctx.calls_to("add_edge"):
        src, tgt = _edge_end(call, 0), _edge_end(call, 1)
        if src and tgt:
            s, t = name_to_id.get(src, src), name_to_id.get(tgt, tgt)
            result.connections.append(Connection(id=connection_id(s, t), source=s, target=t,
                                                 provenance=Provenance.INFERRED,
                                                 source_adapter=ADAPTER))
    for call in ctx.calls_to("add_conditional_edges"):
        src = _edge_end(call, 0)
        mapping = call.node.args[2] if len(call.node.args) > 2 else call.kwarg("path_map")
        targets = _literal_list(mapping) if not isinstance(mapping, ast.Dict) else \
            [ast.unparse(v) if not _literal_str(v) else _literal_str(v) for v in mapping.values]
        if src and targets:
            for tgt in targets:
                tgt = _special(tgt.strip("'\""))
                s, t = name_to_id.get(src, src), name_to_id.get(tgt, tgt)
                result.connections.append(Connection(id=connection_id(s, t, "conditional"),
                                                     source=s, target=t, condition="conditional",
                                                     provenance=Provenance.INFERRED,
                                                     source_adapter=ADAPTER))
        elif src:
            result.unresolved.append(UnresolvedItem(
                kind="dynamic_conditional_edges", location=f"{call.file}:{call.line}",
                message=f"targets of add_conditional_edges from '{src}' are not literal",
                source_adapter=ADAPTER))
    enrich(ctx, result, name_to_id)
    return result


def _special(name: str) -> str:
    return {"START": "__start__", "END": "__end__"}.get(name.split(".")[-1], name)


def _edge_end(call: CallInfo, index: int) -> str | None:
    if index >= len(call.node.args):
        return None
    node = call.node.args[index]
    s = _literal_str(node)
    if s:
        return s
    text = ast.unparse(node)
    return {"START": "__start__", "END": "__end__"}.get(text.split(".")[-1])


def _tools_by_symbol(ctx: InspectContext) -> dict[str, list[str]]:
    """`llm.bind_tools([a, b])` inside a function -> {function_name: ["a", "b"]}"""
    out: dict[str, list[str]] = {}
    for m in ctx.modules:
        for fn in ast.walk(m.tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for n in ast.walk(fn):
                if isinstance(n, ast.Call) and m.resolve(n.func).endswith("bind_tools") and n.args:
                    tools = _literal_list(n.args[0])
                    if tools:
                        out[fn.name] = tools
        # module-level bind_tools: tools = llm.bind_tools([...]) -> attribute to all node fns
        for n in ast.walk(m.tree):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and \
                    m.resolve(n.value.func).endswith("bind_tools") and n.value.args:
                tools = _literal_list(n.value.args[0])
                if tools:
                    out.setdefault("__module__:" + m.rel, tools)
    return out


def _models_by_symbol(ctx: InspectContext) -> dict[str, ModelConfig]:
    out: dict[str, ModelConfig] = {}
    for m in ctx.modules:
        module_cfg: ModelConfig | None = None
        for n in ast.walk(m.tree):
            if isinstance(n, ast.Call):
                cls = m.resolve(n.func).split(".")[-1]
                if cls in MODEL_CLASSES:
                    cfg = _model_config(cls, n)
                    module_cfg = module_cfg or cfg
        if module_cfg is not None:
            for fn in ast.walk(m.tree):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.setdefault(fn.name, module_cfg)
    return out


def _model_config(cls: str, call: ast.Call) -> ModelConfig:
    cfg = ModelConfig(provider=MODEL_CLASSES.get(cls))
    for kw in call.keywords:
        if kw.arg in ("model", "model_name") and _literal_str(kw.value):
            cfg.model = _literal_str(kw.value)
        elif kw.arg in ("temperature", "top_p", "max_tokens", "seed") and \
                isinstance(kw.value, ast.Constant):
            setattr(cfg, kw.arg, kw.value.value)
    if cfg.model is None and call.args and _literal_str(call.args[0]):
        cfg.model = _literal_str(call.args[0])
    return cfg
