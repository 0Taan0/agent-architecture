"""agent-architect CLI: inspect / digest / discover / review / drift / plan / apply / run / serve."""
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
    for name in ("inspect", "digest", "discover", "review", "drift", "plan", "apply", "run"):
        s = sub.add_parser(name)
        s.add_argument("path")
    sub.choices["inspect"].add_argument("--save", action="store_true",
                                        help="write architecture.yaml (import)")
    sub.choices["discover"].add_argument("--force", action="store_true",
                                         help="ask the model even if the code is unchanged")
    sub.choices["discover"].add_argument("--model", help="model id (default: claude-opus-5-5)")
    rv = sub.choices["review"]
    rv.add_argument("--accept", nargs="+", default=[], metavar="RULE", help="accept these rules")
    rv.add_argument("--reject", nargs="+", default=[], metavar="RULE", help="reject these rules")
    rv.add_argument("--accept-all", action="store_true",
                    help="accept every waiting rule that has no error and no warning")
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
    if args.cmd == "digest":
        from core.discovery import build_digest
        print(build_digest(project).render(), end="")
        return 0
    if args.cmd == "discover":
        return _discover(project, args)
    if args.cmd == "review":
        return _review(project, args)
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


def _discover(project: ProjectContext, args: argparse.Namespace) -> int:
    # Imported here: only this command needs the discovery layer and the LLM SDK
    from core.discovery.agent import DEFAULT_MODEL, AnthropicLLM, discover
    from core.inspector.rules import MAPPING_FILE, save_mapping
    try:
        llm = AnthropicLLM(args.model or DEFAULT_MODEL)
    except ImportError:
        print('discover needs the Anthropic SDK: pip install "agent-architect[llm]"', file=sys.stderr)
        return 2
    found = discover(project, llm, force=args.force)
    if found.up_to_date:
        print(f"{MAPPING_FILE} matches the code, nothing to do (use --force to ask again)")
        return 0
    for issue in found.issues:
        label = "dropped" if issue.severity.value == "error" else "warning"
        print(f"{label}: {issue}", file=sys.stderr)
    if found.mapping is None:
        print("the model did not produce a usable mapping", file=sys.stderr)
        return 1
    save_mapping(project.root, found.mapping)
    rules = found.mapping.rules()
    proposed = sum(r.status.value == "proposed" for r in rules)
    print(f"wrote {MAPPING_FILE}: {len(rules)} rules, {proposed} waiting for review "
          f"({found.llm_calls} model calls)")
    print("review: set `status: accepted` or `status: rejected` on each rule")
    return 0


def _review(project: ProjectContext, args: argparse.Namespace) -> int:
    from core.discovery.review import acceptable, decide, review
    from core.inspector.rules import MAPPING_FILE, Status
    try:
        if args.accept_all:
            args.accept = [*args.accept, *acceptable(review(project))]
        if args.accept:
            decide(project, args.accept, Status.ACCEPTED)
        if args.reject:
            decide(project, args.reject, Status.REJECTED)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    state = review(project)
    if not state.exists:
        print(f"no {MAPPING_FILE} - run `discover` first", file=sys.stderr)
        return 2
    for issue in state.issues:
        print(f"{issue.severity.value}: {issue}")
    if state.code_changed:
        print("the code changed since these rules were discovered - run `discover` again")
    print(f"{len(state.rules)} rules, {len(state.waiting())} waiting for review")
    for rule in state.rules:
        print(f"\n[{rule.status.value}] {rule.id}  ({rule.kind}, {rule.matches} matches)")
        print(f"    {rule.text}")
        for label, value in (("yields", ", ".join(rule.yields)), ("evidence", ", ".join(rule.evidence)),
                             ("why", rule.rationale)):
            if value:
                print(f"    {label}: {value}")
        for issue in rule.issues:
            print(f"    {issue.severity.value}: {issue.message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
