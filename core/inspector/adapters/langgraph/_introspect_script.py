"""Standalone script injected into the TARGET interpreter. Must not import `core`.

Usage: python _introspect_script.py <project_root> <module> [<module> ...]
Prints exactly one JSON line as the last line of stdout.
"""
import importlib
import io
import json
import sys
import traceback
from contextlib import redirect_stdout


def _graph_json(obj):
    g = obj.get_graph()
    if hasattr(g, "to_json"):
        return g.to_json()
    nodes = [{"id": nid, "type": type(n.data).__name__ if hasattr(n, "data") else None}
             for nid, n in getattr(g, "nodes", {}).items()]
    edges = [{"source": e.source, "target": e.target,
              "conditional": bool(getattr(e, "conditional", False)),
              "data": getattr(e, "data", None)} for e in getattr(g, "edges", [])]
    return {"nodes": nodes, "edges": edges}


def _state_schema(obj):
    for attr in ("builder", "_builder"):
        b = getattr(obj, attr, None)
        schema = getattr(b, "schema", None) or getattr(b, "state_schema", None)
        if schema is not None:
            return getattr(schema, "__name__", str(schema))
    schema = getattr(obj, "schema", None)
    return getattr(schema, "__name__", None) if schema is not None else None


def main():
    root, modules = sys.argv[1], sys.argv[2:]
    sys.path.insert(0, root)
    out = {"graphs": [], "errors": [], "framework_versions": {}}
    try:
        import langgraph
        out["framework_versions"]["langgraph"] = getattr(langgraph, "__version__", "unknown")
        try:
            from importlib.metadata import version
            out["framework_versions"]["langgraph"] = version("langgraph")
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"langgraph not importable in {sys.executable}: {exc}")
    for mod_name in modules:
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):       # user code may print; keep stdout clean
                mod = importlib.import_module(mod_name)
        except BaseException:  # noqa: BLE001 - SystemExit from user main() too
            out["errors"].append(f"{mod_name}: " + traceback.format_exc().strip().splitlines()[-1])
            continue
        items = [(n, o) for n, o in vars(mod).items()
                 if not n.startswith("_") and not isinstance(o, type)]
        compiled_builders = {id(getattr(o, "builder", None)) for _, o in items
                             if hasattr(o, "get_graph")}
        for name, obj in items:
            candidate = obj
            if not hasattr(candidate, "get_graph") and hasattr(candidate, "compile") \
                    and hasattr(candidate, "add_node"):
                if id(candidate) in compiled_builders:
                    continue                 # its compiled graph is introspected instead
                try:                         # uncompiled StateGraph -> compile a copy
                    with redirect_stdout(buf):
                        candidate = candidate.compile()
                except Exception as exc:  # noqa: BLE001
                    out["errors"].append(f"{mod_name}.{name}: compile() failed: {exc}")
                    continue
            if not hasattr(candidate, "get_graph"):
                continue
            try:
                with redirect_stdout(buf):
                    gj = _graph_json(candidate)
            except Exception as exc:  # noqa: BLE001
                out["errors"].append(f"{mod_name}.{name}: get_graph() failed: {exc}")
                continue
            nodes = gj.get("nodes", [])
            edges = gj.get("edges", [])
            norm_nodes = []
            for n in nodes:
                nid = n["id"] if isinstance(n, dict) else str(n)
                norm_nodes.append({"id": nid, "type": (n.get("type") if isinstance(n, dict) else None)})
            norm_edges = []
            for e in edges:
                if isinstance(e, dict):
                    norm_edges.append({"source": e.get("source"), "target": e.get("target"),
                                       "conditional": bool(e.get("conditional")),
                                       "data": e.get("data")})
                else:
                    norm_edges.append({"source": e[0], "target": e[1], "conditional": False,
                                       "data": None})
            out["graphs"].append({"module": mod_name, "symbol": name, "nodes": norm_nodes,
                                  "edges": norm_edges, "state_schema": _state_schema(candidate)})
    sys.stdout.write("\n" + json.dumps(out) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
