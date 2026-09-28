from pathlib import Path

import yaml

from core.inspector import InspectContext, inspect_project
from core.inspector.adapters import (
    ConventionsAdapter,
    DeclarationsAdapter,
    LangGraphAdapter,
    ProjectLocalAdapter,
)
from core.inspector.base import Confidence
from core.models import Provenance, from_yaml

EXPECTED = Path(__file__).resolve().parents[1] / "examples" / "expected"


def _expected(name):
    return from_yaml((EXPECTED / f"{name}.yaml").read_text())


def test_project_local_matches_expected_card(prozess_mas):
    rep = inspect_project(prozess_mas, [ProjectLocalAdapter()])
    assert rep.model == _expected("prozess-mas")
    names = {a.name for a in rep.model.agents}
    assert names == {"ProcessAnalyzerAgent", "ImprovementProposerAgent", "CriticAgent"}
    assert all(a.provenance == Provenance.DECLARED for a in rep.model.agents)
    assert {u.kind for u in rep.model.unresolved} == {"dynamic_tool_registration"}
    # inspect.py for the user's own MAS must stay small (target: ~50 lines)
    lines = [ln for ln in (prozess_mas.root / ".architecture/inspect.py").read_text().splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    assert len(lines) <= 50


def test_inspect_twice_gives_same_ids(prozess_mas):
    a = inspect_project(prozess_mas, [ProjectLocalAdapter()]).model
    b = inspect_project(prozess_mas, [ProjectLocalAdapter()]).model
    assert [x.id for x in a.agents] == [x.id for x in b.agents]


def test_declarations_reads_decorator_and_reports_declaration_drift(decorated):
    rep = inspect_project(decorated, [DeclarationsAdapter()])
    assert rep.model == _expected("decorated")
    researcher = rep.model.agent_by_name("researcher")
    assert researcher.tools == ["web_search"]
    assert researcher.model.model == "claude-sonnet-4-6"
    assert rep.model.schema(researcher.output_schema).name == "ResearchResult"
    assert len(rep.model.connections) == 1        # routes_to=["writer"] resolved by name
    assert any(u.kind == "declaration_drift" for u in rep.model.unresolved)


def test_conventions_are_inferred_only(prozess_mas):
    rep = inspect_project(prozess_mas, [ConventionsAdapter()])
    assert rep.adapters[0].confidence == Confidence.WEAK
    assert rep.model.agents and all(a.provenance == Provenance.INFERRED for a in rep.model.agents)


def test_merge_priority_declared_over_inferred(prozess_mas):
    rep = inspect_project(prozess_mas)   # project_local + conventions both run
    for a in rep.model.agents:
        assert a.provenance == Provenance.DECLARED
        assert "project_local" in a.source_adapter and "conventions" in a.source_adapter


def test_langgraph_ast_fallback_is_inferred(langgraph_demo):
    rep = inspect_project(langgraph_demo, [LangGraphAdapter(allow_subprocess=False)])
    m = rep.model
    assert {a.name for a in m.agents} == {"research", "verify"}
    assert all(a.provenance == Provenance.INFERRED for a in m.agents)
    assert m.schema_by_name("State") is not None
    ends = {(m.agent(c.source).name if m.agent(c.source) else c.source,
             m.agent(c.target).name if m.agent(c.target) else c.target) for c in m.connections}
    assert ("__start__", "research") in ends and ("verify", "__end__") in ends


def test_langgraph_subprocess_matches_expected_when_installed(langgraph_demo):
    try:
        import langgraph  # noqa: F401
    except ImportError:
        import pytest
        pytest.skip("langgraph not installed in this interpreter")
    rep = inspect_project(langgraph_demo, [LangGraphAdapter()])
    assert rep.model == _expected("langgraph-demo")
    assert all(a.provenance == Provenance.CONFIRMED for a in rep.model.agents)
    # confirmed and fallback paths must agree on IDs (stable identity across mechanisms)
    fallback = inspect_project(langgraph_demo, [LangGraphAdapter(allow_subprocess=False)]).model
    assert {a.id for a in fallback.agents} == {a.id for a in rep.model.agents}


def test_ctx_resolves_dotted_bases(prozess_mas):
    ctx = InspectContext(prozess_mas)
    assert {c.name for c in ctx.classes_inheriting("core.base.BaseAgent")} == \
        {"ProcessAnalyzerAgent", "ImprovementProposerAgent", "CriticAgent"}
    assert {c.name for c in ctx.classes_inheriting("BaseAgent")} == \
        {"ProcessAnalyzerAgent", "ImprovementProposerAgent", "CriticAgent"}
    calls = ctx.calls_to("registry.register")
    assert calls and calls[0].file == "core/registry.py"


def test_expected_yaml_files_parse():
    for p in EXPECTED.glob("*.yaml"):
        assert yaml.safe_load(p.read_text())["agents"]
