"""Discovery agent: an LLM reads the fact digest and proposes mapping rules.

The LLM runs only here. It never decides what the architecture is: it proposes rules,
`validate` checks them against the code, and a rule with an error is dropped. Every
rule leaves this module as `proposed`; only the user accepts a rule.

The loop is bounded: at most `max_rounds` proposals and MAX_LLM_CALLS calls in total.
"""
from __future__ import annotations

import re
from typing import Protocol

import yaml
from pydantic import BaseModel, Field, ValidationError

from core.discovery.digest import Digest, build_digest
from core.discovery.validate import Issue, Severity, errors, validate
from core.inspector.context import InspectContext
from core.inspector.rules import (
    Mapping,
    Meta,
    Rule,
    Status,
    apply_mapping,
    dump_mapping,
    load_mapping,
    parse_mapping,
)
from core.models import ProjectContext
from core.ownership.hashing import canonical_json

PROMPT_VERSION = 1
DEFAULT_MODEL = "claude-opus-5-5"
MAX_LLM_CALLS = 8            # proposals plus file reads
MAX_EXCERPTS_PER_REPLY = 10
MAX_EXCERPT_LINES = 200

SYSTEM_PROMPT = """\
You map the code base of a multi-agent system to a small set of declarative rules.

A deterministic engine applies your rules to the code with static analysis (the Python
AST, nothing is executed) and derives the architecture map from them: the agents, their
inputs, outputs and tools, and the connections between agents. You never list agents or
connections yourself. You describe HOW they can be recognised in this code base, so the
map stays correct when the code changes.

# What you get

A digest of the code base. Per module it lists imports, module-level assignments,
classes (bases, decorators, attributes, method signatures), functions and calls.
`#L12` is a line number. In `calls:`, `pkg.Graph().add_edge` means the method `add_edge`
called on an object created by `pkg.Graph(...)`. Only calls whose callee is certain are
listed, and rules can only match those.

# How you answer

Every reply contains exactly one fenced block and nothing that needs an answer.

To read source code before you decide, request up to 10 excerpts:

```read
path/to/file.py:1-80
```

To propose the rules, reply with the complete mapping:

```yaml
version: 1
agents: [...]
connections: [...]
unresolved: [...]
descriptions: {...}
```

Read code only when the digest is not enough. If the engine reports issues with your
proposal, fix exactly those rules and send the complete mapping again. An error has to
be fixed, otherwise the rule is dropped. A warning means the rule works, but something
it touched is not readable statically: fix it if the rule is too broad, for example with
`ignore` or `exclude`, and leave it if the code really is dynamic.

# The rule language

Every rule has an `id` (short, unique, kebab-case), an `evidence` list with at least one
`file:line` reference that shows the pattern in the code, and a one-sentence
`rationale`. Evidence has to lie in a file where the rule matches, or in the file that
defines the class or function the rule names. Evidence anywhere else is rejected.

## agents

A rule selects classes or call sites. Use exactly one selector:

- `inherits: pkg.base.BaseAgent`  classes that subclass it, directly or indirectly
- `decorator: agent`              classes with this decorator
- `has_attr: TOOLS`               classes that define this class attribute
- `name_suffix: Agent`            classes whose name ends like this
- `classes: [Planner, Critic]`    exactly these classes, when no pattern exists
- `calls: pkg.Agent`              every call of this callee is one agent, for agents that
                                  are created or registered by a call

`exclude: [BaseAgent]` skips classes by name.

Then say where each property is read from. Every property is optional and takes exactly
one source:

- `name`           default: the class name, or the variable the call is assigned to
- `input_schema`   the type the agent receives
- `output_schema`  the type the agent returns
- `tools`          a literal list
- `model`          a string literal
- `impl`           call rules only: the class or function that implements the agent

Sources:

- `{attr: TOOLS}`         class attribute
- `{param_of: run}`       annotation of the first parameter of method `run`
- `{returns_of: run}`     return annotation of method `run`
- `{arg: 0}`              positional argument of the matched call
- `{kwarg: tools}`        keyword argument of the matched call
- `{assigned: true}`      variable the result of the matched call is assigned to

When `impl` is a function, use `__call__` as the method name to read its own signature:
`input_schema: {param_of: __call__}`.

## connections

A rule yields directed edges between agents. Use exactly one kind:

- `sequence: pkg.pipeline.PIPELINE`  a module-level list; consecutive items are connected
- `calls: pkg.Graph().add_edge` with `source` and `target`  every call is an edge.
  `source` and `target` are sources as above (`arg`, `kwarg`), or `{enclosing: true}`
  for the agent class that contains the call. A list argument yields one edge per item,
  a dict argument one edge per value with the key as the condition.
- `calls: pkg.Crew` with `chain: {kwarg: agents}`  a list argument, connected in order
- `edges: [[Planner, Critic]]`  explicit pairs, only when the code shows the flow but no
  rule above can express it

Endpoints are matched by agent name, class or function name, or the variable an agent is
assigned to. `ignore: [START, END]` lists endpoints that are not agents.

## unresolved

Mark what cannot be known statically instead of guessing:

```yaml
- id: dynamic-tools
  calls: pkg.registry.register
  kind: dynamic_tool_registration
  message: tools registered at runtime are not visible statically
```

## descriptions

`descriptions` maps an agent name to one or two plain sentences about what the agent
does, written for a reader who does not know the code.

# What matters

- Prefer a pattern over a list. One `inherits` rule that covers ten agents is better
  than ten names, because it still holds when an agent is added.
- Be incomplete rather than wrong. If the code does not show something, leave it out or
  add an `unresolved` rule. A rule that matches nothing is rejected.
- Schemas, tools and models are read from the code by the engine. Never write their
  values into a rule.
- If an existing mapping is given, keep every rule that still holds, unchanged and with
  its `id`, and only change what the code no longer supports.
"""


class LLM(Protocol):
    """The one thing discovery needs from a model: a reply to a conversation."""
    model: str

    def complete(self, system: str, messages: list[dict[str, str]]) -> str: ...


class AnthropicLLM:
    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        # Imported here so everything except `discover` works without the SDK
        import anthropic
        self.model = model
        self._client = anthropic.Anthropic()

    def complete(self, system: str, messages: list[dict[str, str]]) -> str:
        response = self._client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=messages,
            output_config={"effort": "medium"},
            # The digest is resent on every turn, so cache the conversation prefix
            cache_control={"type": "ephemeral"},
            # If a safety classifier declines, the API retries on a fallback model
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("the model declined the discovery request")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("the model's reply was cut off at max_tokens")
        return "".join(block.text for block in response.content if block.type == "text")


class Discovery(BaseModel):
    mapping: Mapping | None = None        # None: the model never produced a usable mapping
    # Errors: why rules were dropped. Warnings: what the user should look at in the review.
    issues: list[Issue] = Field(default_factory=list)
    rounds: int = 0
    llm_calls: int = 0
    up_to_date: bool = False              # the code has not changed, no LLM call was made


def discover(project: ProjectContext, llm: LLM, *, max_rounds: int = 3,
             force: bool = False) -> Discovery:
    """Propose mapping rules for `project`. Does not write anything; the caller saves."""
    ctx = InspectContext(project, adapter="rules")
    digest = build_digest(project, ctx=ctx)
    previous = _previous_mapping(project)
    if previous and previous.meta and previous.meta.digest_hash == digest.hash and not force:
        return Discovery(mapping=previous, up_to_date=True)

    messages = [{"role": "user", "content": _task(digest, previous)}]
    best: tuple[Mapping, list[Issue]] | None = None
    issues: list[Issue] = []
    rounds = calls = 0
    while rounds < max_rounds and calls < MAX_LLM_CALLS:
        reply = llm.complete(SYSTEM_PROMPT, messages)
        calls += 1
        messages.append({"role": "assistant", "content": reply})
        requests = _block(reply, "read")
        if requests is not None:
            messages.append({"role": "user", "content": _excerpts(project, requests)})
            continue
        rounds += 1
        proposal = _block(reply, "yaml")
        try:
            mapping = parse_mapping(proposal or "")
        except (yaml.YAMLError, ValidationError) as exc:
            issues = [Issue(kind="mapping_invalid", message=f"the mapping is not valid: {exc}")]
        else:
            if proposal is None:
                issues = [Issue(kind="no_proposal",
                                message="the reply contains neither a ```yaml nor a ```read block")]
            elif not mapping.rules():
                issues = [Issue(kind="no_proposal", message="the mapping contains no rules")]
            else:
                issues = validate(ctx, mapping)
                best = (mapping, issues)
                # Warnings do not need another round
                if not errors(issues):
                    break
        messages.append({"role": "user", "content": _feedback(issues)})

    if best is None:
        issues = issues or [Issue(kind="no_proposal",
                                  message=f"no mapping was proposed within {calls} model calls")]
        return Discovery(issues=issues, rounds=rounds, llm_calls=calls)
    mapping, remaining = _without_invalid_rules(ctx, *best)
    for rule in mapping.rules():
        rule.status = Status.PROPOSED
    _carry_over_status(mapping, previous)
    mapping.meta = Meta(digest_hash=digest.hash, model=llm.model, prompt_version=PROMPT_VERSION)
    return Discovery(mapping=mapping, issues=remaining, rounds=rounds, llm_calls=calls)


# Messages to the model
def _task(digest: Digest, previous: Mapping | None) -> str:
    task = f"Here is the digest of the code base.\n\n{digest.render()}"
    if previous:
        task += ("\nThis is the existing mapping. The code has changed since it was written.\n\n"
                 f"```yaml\n{dump_mapping(previous)}```\n")
    return task + "\nPropose the mapping rules."


def _feedback(issues: list[Issue]) -> str:
    lines = "\n".join(f"- [{issue.severity.value}] {issue}" for issue in issues)
    return ("The engine applied your mapping and reported these issues:\n\n"
            f"{lines}\n\nFix them and send the complete mapping again.")


def _block(reply: str, language: str) -> str | None:
    # Content of the last fenced block of that language, None if there is none
    blocks = re.findall(rf"```{language}[ \t]*\n(.*?)```", reply, flags=re.DOTALL)
    return blocks[-1] if blocks else None


def _excerpts(project: ProjectContext, requests: str) -> str:
    wanted = [line.strip() for line in requests.splitlines() if line.strip()]
    parts = [_excerpt(project, request) for request in wanted[:MAX_EXCERPTS_PER_REPLY]]
    if len(wanted) > MAX_EXCERPTS_PER_REPLY:
        parts.append(f"Only the first {MAX_EXCERPTS_PER_REPLY} excerpts are returned per reply.")
    return "\n\n".join(parts) or "The read block was empty."


def _excerpt(project: ProjectContext, request: str) -> str:
    match = re.fullmatch(r"(.+?)(?::(\d+)(?:-(\d+))?)?", request)
    rel, start, end = match.group(1), int(match.group(2) or 1), match.group(3)
    path = project.abs(rel)
    # Only files inside the project are readable
    if not project.is_inside(path) or not path.is_file():
        return f"{request}: no such file in the project"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    stop = min(int(end) if end else len(lines), start + MAX_EXCERPT_LINES - 1, len(lines))
    body = "\n".join(f"{n}: {lines[n - 1]}" for n in range(max(start, 1), stop + 1))
    return f"{rel}:{start}-{stop} (file has {len(lines)} lines)\n{body}"


# Finishing a proposal
def _previous_mapping(project: ProjectContext) -> Mapping | None:
    try:
        return load_mapping(project.root)
    except (yaml.YAMLError, ValidationError):
        # A broken file is replaced by the new proposal
        return None


def _without_invalid_rules(ctx: InspectContext, mapping: Mapping,
                           issues: list[Issue]) -> tuple[Mapping, list[Issue]]:
    # Drop rules with errors. Removing a rule can break another one, so repeat until clean.
    # Returns the errors that caused a drop, followed by the warnings of what is kept.
    dropped: list[Issue] = []
    failed = errors(issues)
    while failed:
        dropped += failed
        bad = {issue.rule for issue in failed}
        mapping = mapping.model_copy(update={
            "agents": [r for r in mapping.agents if r.id not in bad],
            "connections": [r for r in mapping.connections if r.id not in bad],
            "unresolved": [r for r in mapping.unresolved if r.id not in bad],
        })
        names = {a.name for a in apply_mapping(ctx, mapping).result.agents}
        mapping.descriptions = {k: v for k, v in mapping.descriptions.items() if k in names}
        issues = validate(ctx, mapping)
        failed = [i for i in errors(issues) if i.rule]
    return mapping, dropped + [i for i in issues if i.severity == Severity.WARNING]


def _carry_over_status(mapping: Mapping, previous: Mapping | None) -> None:
    # A rule the user already accepted or rejected keeps that decision if it is unchanged
    decided = {_content(rule): rule.status for rule in (previous.rules() if previous else [])}
    for rule in mapping.rules():
        rule.status = decided.get(_content(rule), rule.status)


def _content(rule: Rule) -> str:
    # What a rule says, without its id and the fields that only document it
    data = rule.model_dump(mode="json", exclude={"id", "status", "evidence", "rationale"})
    return type(rule).__name__ + canonical_json(data)
