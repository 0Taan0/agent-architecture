import pytest

from core.discovery import discover, validate
from core.discovery.agent import PROMPT_VERSION, SYSTEM_PROMPT
from core.discovery.review import acceptable, decide, review
from core.inspector import InspectContext, inspect_project
from core.inspector.rules import RulesAdapter, Status, load_mapping, parse_mapping, save_mapping
from core.models import ProjectContext, Provenance

# Model replies are written by hand here, so the tests run without any API access
GOOD = """\
The agents share a base class.

```yaml
version: 1
agents:
- id: agents
  status: accepted
  evidence: [core/base.py:6]
  rationale: Every agent subclasses BaseAgent.
  inherits: core.base.BaseAgent
  input_schema: {param_of: run}
  output_schema: {returns_of: run}
  tools: {attr: TOOLS}
connections:
- id: pipeline
  evidence: [pipeline.py:5]
  sequence: pipeline.PIPELINE
descriptions:
  CriticAgent: Checks a proposal and accepts or rejects it.
```
"""
BAD = GOOD.replace("core.base.BaseAgent", "core.base.Agent").replace("pipeline.py:5", "pipeline.py:99")
READ = "I need to see the pipeline first.\n\n```read\npipeline.py:1-3\n../secret.txt\n```\n"


class FakeLLM:
    model = "fake-model"

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.seen: list[list[dict]] = []

    def complete(self, system: str, messages: list[dict]) -> str:
        assert system == SYSTEM_PROMPT
        self.seen.append([dict(m) for m in messages])
        return self.replies.pop(0)


@pytest.fixture
def project(tmp_path, prozess_mas) -> ProjectContext:
    # A copy of the example without its mapping, so discovery starts from nothing
    for path in prozess_mas.python_files():
        target = tmp_path / prozess_mas.rel(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path.read_text())
    return ProjectContext.open(tmp_path)


def _validate(project: ProjectContext, mapping_yaml: str) -> list[tuple[str, str, str]]:
    issues = validate(InspectContext(project, adapter="rules"), parse_mapping(mapping_yaml))
    return [(i.severity.value, i.kind, str(i)) for i in issues]


def test_validate_names_the_rule_and_the_reason(project):
    assert _validate(project, """
agents:
- {id: a, inherits: core.base.BaseAgent, evidence: ["core/base.py:6"]}
- {id: a, inherits: core.base.Missing, evidence: ["core/base.py:999", "nowhere.py:1", "oops"]}
connections:
- {id: c, edges: [[CriticAgent, Ghost]]}
- {id: skipped, status: rejected, sequence: no.such.LIST}
descriptions: {Ghost: does not exist}
""") == [
        ("error", "duplicate_id", "rule a: rule id is used more than once"),
        ("warning", "unresolved_connection_target", "rule c: 'Ghost' is not a known agent"),
        ("error", "rule_stale", "rule c: matches nothing in the code"),
        ("error", "evidence_invalid", "rule a: evidence 'core/base.py:999': the file has 11 lines"),
        ("error", "evidence_invalid", "rule a: evidence 'nowhere.py:1': no such file in the project"),
        ("error", "evidence_invalid", "rule a: evidence 'oops' is not of the form file:line"),
        ("error", "evidence_missing", "rule c: no evidence: name at least one file:line that shows the pattern"),
        ("error", "description_unknown", "description for 'Ghost', but no rule yields an agent with that name"),
    ]
    assert _validate(project, GOOD.split("```yaml")[1][:-4]) == []


def test_evidence_has_to_lie_where_the_rule_applies(project):
    def issues(evidence: str) -> list[str]:
        return [text for _, _, text in _validate(
            project, f"agents:\n- {{id: a, inherits: core.base.BaseAgent, evidence: [{evidence}]}}\n")]

    # where the rule matches, and where the base class it names is defined
    assert issues('"agents/critic.py:9"') == []
    assert issues('"core/base.py:6"') == []
    # a real line, but the rule has nothing to do with that file
    assert issues('"core/registry.py:5"') == [
        "rule a: evidence 'core/registry.py:5' is unrelated: the rule matches in "
        "agents/analyzer.py, agents/critic.py, agents/proposer.py, core/base.py"]


def test_unreadable_values_are_warnings_and_keep_the_rule(project):
    # One edge points at something that is not an agent: the other edge still counts
    mapping = GOOD.replace("sequence: pipeline.PIPELINE",
                           "edges: [[ProcessAnalyzerAgent, CriticAgent], [CriticAgent, Reviewer]]")
    llm = FakeLLM(mapping)
    found = discover(project, llm)
    assert (found.rounds, found.llm_calls) == (1, 1)          # a warning needs no second round
    assert [(i.severity.value, str(i)) for i in found.issues] == \
        [("warning", "rule pipeline: 'Reviewer' is not a known agent")]
    assert [r.id for r in found.mapping.rules()] == ["agents", "pipeline"]


def test_discover_reads_code_fixes_issues_and_proposes(project):
    llm = FakeLLM(READ, BAD, GOOD)
    found = discover(project, llm)
    assert (found.rounds, found.llm_calls, found.issues, found.up_to_date) == (2, 3, [], False)

    # 1st call: the digest. 2nd: the excerpt it asked for. 3rd: the engine's issues.
    assert "## agents/analyzer.py" in llm.seen[0][0]["content"]
    excerpt = llm.seen[1][-1]["content"]
    assert "pipeline.py:1-3 (file has 5 lines)\n1: from agents.analyzer import" in excerpt
    assert "../secret.txt: no such file in the project" in excerpt
    feedback = llm.seen[2][-1]["content"]
    assert "- [error] rule agents: matches nothing in the code" in feedback
    assert "rule pipeline: evidence 'pipeline.py:99': the file has 5 lines" in feedback

    # The model cannot accept its own rules, and the mapping records what it was made from
    assert {r.status.value for r in found.mapping.rules()} == {"proposed"}
    assert found.mapping.meta.model == "fake-model"
    assert found.mapping.meta.prompt_version == PROMPT_VERSION
    assert found.mapping.meta.digest_hash.startswith("sha256:")
    assert validate(InspectContext(project, adapter="rules"), found.mapping) == []


def test_unchanged_code_needs_no_model_call(project):
    save_mapping(project.root, discover(project, FakeLLM(GOOD)).mapping)
    silent = FakeLLM()
    again = discover(project, silent)
    assert again.up_to_date and again.llm_calls == 0 and silent.seen == []


def test_a_decision_of_the_user_survives_a_new_proposal(project):
    mapping = discover(project, FakeLLM(GOOD)).mapping
    mapping.agents[0].status = Status.ACCEPTED
    mapping.connections[0].status = Status.REJECTED
    save_mapping(project.root, mapping)

    # The model renames a rule and is shown the existing mapping, the decisions stay
    llm = FakeLLM(GOOD.replace("id: pipeline", "id: order"))
    found = discover(project, llm, force=True)
    assert "This is the existing mapping" in llm.seen[0][0]["content"]
    assert {r.id: r.status.value for r in found.mapping.rules()} == {"agents": "accepted", "order": "rejected"}


def test_rules_that_stay_invalid_are_dropped(project):
    stale = GOOD.replace("pipeline.PIPELINE", "pipeline.NO_SUCH_LIST")
    found = discover(project, FakeLLM(stale, stale, stale, stale), max_rounds=2)
    assert (found.rounds, found.llm_calls) == (2, 2)
    assert [str(i) for i in found.issues] == ["rule pipeline: matches nothing in the code"]
    assert [r.id for r in found.mapping.rules()] == ["agents"]
    assert found.mapping.descriptions == {"CriticAgent": "Checks a proposal and accepts or rejects it."}


def test_no_usable_reply_gives_no_mapping(project):
    found = discover(project, FakeLLM("no block", "```yaml\nagents: [{id: a}]\n```"), max_rounds=2)
    assert found.mapping is None and "the mapping is not valid" in str(found.issues[0])
    assert load_mapping(project.root) is None


# Review
def _proposed(project: ProjectContext) -> ProjectContext:
    save_mapping(project.root, discover(project, FakeLLM(GOOD)).mapping)
    return project


def test_review_shows_what_each_rule_yields(project):
    assert not review(project).exists
    state = review(_proposed(project))
    agents, pipeline = state.rules
    assert (agents.kind, agents.status, agents.matches) == ("agent", Status.PROPOSED, 3)
    assert sorted(agents.yields) == ["CriticAgent", "ImprovementProposerAgent", "ProcessAnalyzerAgent"]
    assert agents.text.startswith("inherits: core.base.BaseAgent")
    assert pipeline.yields == ["ProcessAnalyzerAgent -> ImprovementProposerAgent",
                               "ImprovementProposerAgent -> CriticAgent"]
    assert len(pipeline.element_ids) == 2 and all(i.startswith("conn_") for i in pipeline.element_ids)
    assert [r.id for r in state.waiting()] == ["agents", "pipeline"] and not state.code_changed


def test_a_decision_changes_only_the_status_and_the_provenance(project):
    _proposed(project)
    before = inspect_project(project, [RulesAdapter()]).model
    assert {a.provenance for a in before.agents} == {Provenance.INFERRED}

    state = decide(project, ["agents"], Status.ACCEPTED)
    assert [r.status for r in state.rules] == [Status.ACCEPTED, Status.PROPOSED]
    after = inspect_project(project, [RulesAdapter()]).model
    assert {a.provenance for a in after.agents} == {Provenance.DECLARED}
    assert {c.provenance for c in after.connections} == {Provenance.INFERRED}
    # the mapping still describes the same code, so no new discovery run is needed
    assert discover(project, FakeLLM()).up_to_date

    # a rejected rule yields nothing, but stays in the file
    state = decide(project, ["pipeline"], Status.REJECTED)
    assert inspect_project(project, [RulesAdapter()]).model.connections == []
    assert [r.id for r in state.rules] == ["agents", "pipeline"] and state.waiting() == []


def test_decide_refuses_what_makes_no_sense(project):
    with pytest.raises(ValueError, match="no usable"):
        decide(project, ["agents"], Status.ACCEPTED)
    _proposed(project)
    with pytest.raises(ValueError, match="unknown rule: nope"):
        decide(project, ["nope"], Status.ACCEPTED)
    # the code moves on and the rule matches nothing: it cannot be accepted any more
    (project.root / "pipeline.py").write_text("STEPS = []\n" + "\n" * 4)
    state = review(project)
    assert state.code_changed and [str(i) for i in state.rules[1].issues] == \
        ["rule pipeline: matches nothing in the code"]
    with pytest.raises(ValueError, match="cannot be accepted: pipeline"):
        decide(project, ["pipeline"], Status.ACCEPTED)
    assert acceptable(state) == ["agents"]
    assert decide(project, ["pipeline"], Status.REJECTED).rules[1].status == Status.REJECTED
