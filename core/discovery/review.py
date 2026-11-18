"""Review: the user decides which proposed rules count.

A decision only changes the `status` of rules in mapping.yaml. Accepting a rule turns
what it yields from `inferred` into `declared`; rejecting it removes those elements from
the map but keeps the rule in the file, so discovery does not propose it again.
"""
from __future__ import annotations

import yaml
from pydantic import BaseModel, Field, ValidationError

from core.discovery.digest import build_digest
from core.discovery.validate import Issue, Severity, validate
from core.inspector.context import InspectContext
from core.inspector.rules import (
    MAPPING_FILE,
    AgentRule,
    ConnectionRule,
    Status,
    apply_mapping,
    load_mapping,
    rule_text,
    save_mapping,
)
from core.models import ProjectContext


class RuleReview(BaseModel):
    id: str
    kind: str                     # agent | connection | unresolved
    status: Status
    text: str                     # what the rule says, on one line
    rationale: str | None = None
    evidence: list[str] = Field(default_factory=list)
    yields: list[str] = Field(default_factory=list)        # readable: "Planner", "Planner -> Critic"
    element_ids: list[str] = Field(default_factory=list)   # the same elements, for the canvas
    matches: int = 0
    issues: list[Issue] = Field(default_factory=list)

    @property
    def acceptable(self) -> bool:
        return not any(i.severity == Severity.ERROR for i in self.issues)


class ReviewState(BaseModel):
    exists: bool = False          # is there a mapping.yaml at all
    code_changed: bool = False    # the code differs from what the rules were discovered on
    rules: list[RuleReview] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)       # issues that belong to no rule
    descriptions: dict[str, str] = Field(default_factory=dict)

    def waiting(self) -> list[RuleReview]:
        return [r for r in self.rules if r.status == Status.PROPOSED]


def review(project: ProjectContext) -> ReviewState:
    """Everything the user needs to decide: each rule, what it yields, and what is wrong with it."""
    try:
        mapping = load_mapping(project.root)
    except (yaml.YAMLError, ValidationError) as exc:
        return ReviewState(exists=True, issues=[
            Issue(kind="mapping_invalid", message=f"{MAPPING_FILE} is not valid: {exc}")])
    if mapping is None:
        return ReviewState()

    ctx = InspectContext(project, adapter="rules")
    applied = apply_mapping(ctx, mapping)
    issues = validate(ctx, mapping, applied)
    agents = {a.id: a.name for a in applied.result.agents}
    labels = dict(agents)
    for c in applied.result.connections:
        label = f"{agents.get(c.source, c.source)} -> {agents.get(c.target, c.target)}"
        labels[c.id] = f"{label} [{c.condition}]" if c.condition else label

    rules = []
    for rule in mapping.rules():
        kind = "agent" if isinstance(rule, AgentRule) else (
            "connection" if isinstance(rule, ConnectionRule) else "unresolved")
        ids = applied.elements.get(rule.id, [])
        rules.append(RuleReview(
            id=rule.id, kind=kind, status=rule.status, text=rule_text(rule),
            rationale=rule.rationale, evidence=rule.evidence,
            yields=[labels[i] for i in ids], element_ids=ids,
            matches=applied.matches.get(rule.id, 0),
            issues=[i for i in issues if i.rule == rule.id]))
    # A hand-written mapping has no digest hash: nothing to compare the code against
    known = mapping.meta.digest_hash if mapping.meta else None
    return ReviewState(
        exists=True, rules=rules, descriptions=mapping.descriptions,
        issues=[i for i in issues if i.rule is None],
        code_changed=bool(known) and known != build_digest(project, ctx=ctx).hash)


def decide(project: ProjectContext, rule_ids: list[str], status: Status) -> ReviewState:
    """Set the status of the given rules and save. Raises ValueError if that is not possible."""
    state = review(project)
    # No file, or a file that does not parse: there are no rules to decide on
    if not state.rules:
        raise ValueError(f"no usable {MAPPING_FILE} to review")
    mapping = load_mapping(project.root)
    known = {r.id: r for r in state.rules}
    unknown = [i for i in rule_ids if i not in known]
    if unknown:
        raise ValueError(f"unknown rule: {', '.join(unknown)}")
    if status == Status.ACCEPTED:
        # A rule the validator calls wrong cannot become part of the map
        broken = [i for i in rule_ids if not known[i].acceptable]
        if broken:
            raise ValueError(f"rule has errors and cannot be accepted: {', '.join(broken)}")
    for rule in mapping.rules():
        if rule.id in rule_ids:
            rule.status = status
    save_mapping(project.root, mapping)
    return review(project)


def acceptable(state: ReviewState) -> list[str]:
    """Rules that wait for a decision and have no issue at all: safe to accept in one go."""
    return [r.id for r in state.waiting() if not r.issues]
