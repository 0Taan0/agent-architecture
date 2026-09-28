"""agent-architect CLI: inspect / drift / plan / apply / run / serve."""
from __future__ import annotations

import argparse
import json
import sys

from core.drift import detect_drift
from core.generator import apply_plan, plan_generation
from core.inspector import inspect_project
from core.models import ProjectContext, load_architecture, save_architecture, to_yaml
from core.ownership import load_manifest
from core.runtime import run_project


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent-architect")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("inspect", "drift", "plan", "apply", "run"):
        s = sub.add_parser(name)
        s.add_argument("path")
    sub.choices["inspect"].add_argument("--save", action="store_true",
                                        help="write architecture.yaml (import)")
    sub.choices["apply"].add_argument("--allow-dirty", action="store_true")
    sub.choices["run"].add_argument("--input", default="{}")
    srv = sub.add_parser("serve")
    srv.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("backend.app:app", port=args.port, reload=False)
        return 0
    project = ProjectContext.open(args.path)
    if args.cmd == "inspect":
        rep = inspect_project(project)
        for st in rep.adapters:
            print(f"# {st.name:14s} {st.confidence.value:7s} agents={st.agents} "
                  f"connections={st.connections} unresolved={st.unresolved}", file=sys.stderr)
        if args.save:
            save_architecture(project.root, rep.model)
            print(f"wrote {project.root / 'architecture.yaml'}", file=sys.stderr)
        else:
            print(to_yaml(rep.model))
        return 0
    model = load_architecture(project.root)
    if model is None:
        print("no architecture.yaml - run `inspect --save` or design one first", file=sys.stderr)
        return 2
    if args.cmd == "drift":
        rep = detect_drift(project, model, inspect_project(project).model, load_manifest(project.root))
        for i in rep.visible():
            print(f"[{i.severity.value:10s}] {i.kind:28s} {i.subject}"
                  + (f"  ({i.field}: {i.architecture} -> {i.code})" if i.field else ""))
        return 1 if rep.breaking else 0
    if args.cmd == "plan":
        plan = plan_generation(project, model, load_manifest(project.root))
        for op in plan.operations:
            print(f"{op.kind.value:9s} {op.ownership.value:9s} {op.path}  # {op.reason}")
            if op.diff:
                print(op.diff)
        return 0
    if args.cmd == "apply":
        plan = plan_generation(project, model, load_manifest(project.root))
        res = apply_plan(project, plan, load_manifest(project.root), allow_dirty=args.allow_dirty)
        print(json.dumps(res.model_dump(), indent=2))
        return 1 if res.refused else 0
    if args.cmd == "run":
        res = run_project(project, model, args.input)
        print(json.dumps(res.model_dump(), indent=2, default=str))
        return 0 if res.status == "ok" else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
