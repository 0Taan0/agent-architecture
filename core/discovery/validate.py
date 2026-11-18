"""Deterministic check of a mapping against the code.

The discovery agent may propose anything; nothing is kept unless it survives this check.
Every issue names the rule it belongs to, so the agent can fix exactly that rule.

An error means the rule cannot be trusted: it goes back to the agent and the rule is
dropped if it stays. A warning means the rule works but something it touched is not
readable: the rule is kept and the warning is shown in the review.
"""
from __future__ import annotations

import ast
from enum import Enum

from pydantic import BaseModel

from core.inspector.context import InspectContext
from core.inspector.rules import Applied, Mapping, Rule, Status, apply_mapping

# Problems the rule engine reports that do not make the rule itself wrong
_WARNINGS = {"not_static", "unresolved_connection_target"}
# Rule fields that name a symbol in the code
_SELECTORS = ("inherits", "decorator", "calls", "sequence")


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class Issue(BaseModel):
    kind: str
    message: str
    rule: str | None = None      # rule id, None when the issue is about the file as a whole
    severity: Severity = Severity.ERROR

    def __str__(self) -> str:
        return f"rule {self.rule}: {self.message}" if self.rule else self.message


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == Severity.ERROR]


def validate(ctx: InspectContext, mapping: Mapping, applied: Applied | None = None) -> list[Issue]:
    """Pass `applied` if the rules were already run on this mapping."""
    issues: list[Issue] = []
    seen: set[str] = set()
    for rule in mapping.rules():
        if rule.id in seen:
            issues.append(Issue(kind="duplicate_id", rule=rule.id, message="rule id is used more than once"))
        seen.add(rule.id)

    # Run the rules: a rule that matches nothing is an error, an unreadable value a warning
    applied = applied or apply_mapping(ctx, mapping)
    issues += [Issue(kind=kind, rule=rule_id, message=message,
                     severity=Severity.WARNING if kind in _WARNINGS else Severity.ERROR)
               for rule_id, kind, message in applied.problems]

    for rule in mapping.rules():
        if rule.status == Status.REJECTED:
            continue
        if not rule.evidence:
            issues.append(Issue(kind="evidence_missing", rule=rule.id,
                                message="no evidence: name at least one file:line that shows the pattern"))
        # Evidence has to lie where the rule matched, or where the symbol it names is defined.
        # Explicit edges exist because no pattern expresses them, so the code that shows the
        # flow can be anywhere. A rule that matched nothing is reported as stale instead.
        related = applied.files.get(rule.id, set()) | _definition_files(ctx, rule)
        if getattr(rule, "edges", None) or not applied.matches.get(rule.id):
            related = None
        for ref in rule.evidence:
            problem = _evidence_problem(ctx, ref, related)
            if problem:
                issues.append(Issue(kind="evidence_invalid", rule=rule.id, message=problem))

    names = {a.name for a in applied.result.agents}
    issues += [Issue(kind="description_unknown",
                     message=f"description for {name!r}, but no rule yields an agent with that name")
               for name in mapping.descriptions if name not in names]
    return issues


def _evidence_problem(ctx: InspectContext, ref: str, related: set[str] | None) -> str | None:
    # Evidence has to point at a real line: "path/to/file.py:12"
    rel, _, line = ref.rpartition(":")
    if not rel or not line.isdigit():
        return f"evidence {ref!r} is not of the form file:line"
    path = ctx.project.abs(rel)
    if not ctx.project.is_inside(path) or not path.is_file():
        return f"evidence {ref!r}: no such file in the project"
    count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    if not 1 <= int(line) <= count:
        return f"evidence {ref!r}: the file has {count} lines"
    if related is not None and ctx.project.rel(path) not in related:
        return (f"evidence {ref!r} is unrelated: the rule matches in "
                f"{', '.join(sorted(related)) or 'no file'}")
    return None


def _definition_files(ctx: InspectContext, rule: Rule) -> set[str]:
    # Files that define a class or function the rule's selector names,
    # e.g. the base class of an `inherits` rule
    names: set[str] = set()
    for attr in _SELECTORS:
        value = getattr(rule, attr, None)
        if isinstance(value, str):
            names.update(part.removesuffix("()") for part in value.split("."))
    files = {c.file for c in ctx.classes if c.name in names}
    for m in ctx.modules:
        if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names
               for n in m.tree.body):
            files.add(m.rel)
    return files
