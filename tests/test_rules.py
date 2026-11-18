from pathlib import Path

import pytest
from pydantic import ValidationError

from core.inspector import InspectContext, inspect_project
from core.inspector.rules import (
    RulesAdapter,
    Status,
    apply_mapping,
    dump_mapping,
    load_mapping,
    parse_mapping,
    save_mapping,
)
from core.models import ProjectContext, Provenance, from_yaml

EXPECTED = Path(__file__).resolve().parents[1] / "examples" / "expected"

# A graph-style project: agents are registered by calls, functions implement them
GRAPH_PROJECT = {
    "lib.py": "class Graph:\n    def add_node(self, name, fn): ...\n    def add_edge(self, a, b): ...\n"
              "    def add_router(self, a, routes): ...\nSTART = 'start'\n",
    "state.py": "from pydantic import BaseModel\nclass State(BaseModel):\n    topic: str\n"
                "class Draft(BaseModel):\n    text: str\n",
    "nodes.py": "from state import Draft, State\n"
                "def research(state: State) -> Draft: ...\n"
                "def write(state: Draft) -> Draft: ...\n"
                "def review(state: Draft) -> Draft: ...\n",
    "app.py": "from lib import Graph, START\n"
              "from nodes import research, review, write\n"
              "g = Graph()\n"
              "g.add_node('research', research)\n"
              "g.add_node('write', write)\n"
              "g.add_node('review', review)\n"
              "g.add_edge(START, 'research')\n"
              "g.add_edge('research', 'write')\n"
              "g.add_router('write', {'ok': 'review', 'redo': 'research'})\n"
              "g.add_edge('review', 'publish')\n",
}
GRAPH_MAPPING = """
agents:
- id: nodes
  status: accepted
  calls: lib.Graph().add_node
  name: {arg: 0}
  impl: {arg: 1}
  input_schema: {param_of: __call__}
  output_schema: {returns_of: __call__}
connections:
- id: edges
  status: accepted
  calls: lib.Graph().add_edge
  source: {arg: 0}
  target: {arg: 1}
  ignore: [START]
- id: routers
  calls: lib.Graph().add_router
  source: {arg: 0}
  target: {arg: 1}
"""


def _project(tmp_path, files: dict[str, str]) -> ProjectContext:
    for rel, src in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(src)
    return ProjectContext.open(tmp_path)


def _apply(project: ProjectContext, mapping_yaml: str):
    return apply_mapping(InspectContext(project, adapter="rules"), parse_mapping(mapping_yaml))


def test_mapping_reproduces_the_expected_card(prozess_mas):
    # The same card the hand-written inspect.py yields, without any project code
    report = inspect_project(prozess_mas, [RulesAdapter()])
    expected = (EXPECTED / "prozess-mas.yaml").read_text().replace("project_local", "rules")
    assert report.model == from_yaml(expected)


def test_inspecting_twice_gives_the_same_model(prozess_mas):
    assert inspect_project(prozess_mas, [RulesAdapter()]).model == \
        inspect_project(prozess_mas, [RulesAdapter()]).model


def test_status_decides_provenance(prozess_mas):
    mapping = load_mapping(prozess_mas.root)
    mapping.agents[0].status = Status.PROPOSED
    mapping.connections[0].status = Status.REJECTED
    applied = apply_mapping(InspectContext(prozess_mas, adapter="rules"), mapping)
    assert {a.provenance for a in applied.result.agents} == {Provenance.INFERRED}
    assert applied.result.connections == [] and "pipeline" not in applied.matches
    assert applied.matches == {"agents": 3, "dynamic-tools": 1}


def test_agents_registered_by_calls(tmp_path):
    applied = _apply(_project(tmp_path, GRAPH_PROJECT), GRAPH_MAPPING)
    agents = {a.name: a for a in applied.result.agents}
    assert set(agents) == {"research", "write", "review"}
    assert agents["research"].code_ref.file == "nodes.py" and agents["research"].code_ref.symbol == "research"
    schemas = {s.id: s.name for s in applied.result.schemas}
    assert schemas[agents["research"].input_schema] == "State"
    assert schemas[agents["research"].output_schema] == "Draft"

    names = {a.id: a.name for a in applied.result.agents}
    edges = {(names[c.source], names[c.target], c.condition, c.provenance.value)
             for c in applied.result.connections}
    assert edges == {
        ("research", "write", None, "declared"),
        ("write", "review", "ok", "inferred"),       # dict keys become conditions
        ("write", "research", "redo", "inferred"),
    }
    # START is ignored on purpose, the unknown target is reported, not dropped
    problems = [u for u in applied.result.unresolved if u.kind == "unresolved_connection_target"]
    assert [u.location for u in problems] == ["app.py:10"]
    assert "'publish' is not a known agent" in problems[0].message


def test_agents_created_by_constructor_calls_and_chains(tmp_path):
    project = _project(tmp_path, {
        "lib.py": "class Agent:\n    def __init__(self, **kw): ...\nclass Crew:\n    def __init__(self, **kw): ...\n",
        "app.py": "from lib import Agent, Crew\n"
                  "MODEL = 'm-1'\n"
                  "planner = Agent(role='Planner', tools=['search', lookup], model='m-1')\n"
                  "writer = Agent(role='Writer', model=MODEL)\n"
                  "crew = Crew(agents=[planner, writer])\n",
    })
    applied = _apply(project, """
agents:
- id: agents
  calls: lib.Agent
  name: {kwarg: role}
  tools: {kwarg: tools}
  model: {kwarg: model}
connections:
- id: crew
  calls: lib.Crew
  chain: {kwarg: agents}
""")
    planner, writer = sorted(applied.result.agents, key=lambda a: a.name)
    assert (planner.name, planner.tools, planner.model.model) == ("Planner", ["search", "lookup"], "m-1")
    # MODEL is a variable, not a literal: reported instead of guessed
    assert writer.model is None
    assert any(u.kind == "not_static" and "model" in u.message for u in applied.result.unresolved)
    # the chain refers to the agents by the variables they are assigned to
    assert [(c.source, c.target) for c in applied.result.connections] == [(planner.id, writer.id)]


def test_edges_from_calls_inside_an_agent_class(tmp_path):
    project = _project(tmp_path, {
        "bus.py": "def send(to, msg): ...\n",
        "agents.py": "from bus import send\n"
                     "class Agent: ...\n"
                     "class Planner(Agent):\n    def run(self, x):\n        send(Critic, x)\n"
                     "class Critic(Agent):\n    def run(self, x): ...\n",
    })
    applied = _apply(project, """
agents:
- {id: agents, inherits: agents.Agent}
connections:
- id: sends
  calls: bus.send
  source: {enclosing: true}
  target: {arg: 0}
""")
    by_name = {a.name: a.id for a in applied.result.agents}
    assert [(c.source, c.target) for c in applied.result.connections] == \
        [(by_name["Planner"], by_name["Critic"])]


def test_stale_rule_is_reported(prozess_mas):
    applied = _apply(prozess_mas, "agents:\n- {id: gone, inherits: core.base.NoSuchBase}\n")
    assert applied.matches == {"gone": 0}
    assert [(u.kind, u.location) for u in applied.result.unresolved] == \
        [("rule_stale", ".architecture/mapping.yaml")]


def test_rule_model_rejects_what_it_cannot_mean():
    for bad in (
        "agents:\n- {id: a}\n",                                              # no selector
        "agents:\n- {id: a, inherits: X, decorator: y}\n",                    # two selectors
        "agents:\n- {id: a, inherits: X, tools: {attr: T, kwarg: t}}\n",      # two sources
        "agents:\n- {id: a, inherits: X, colour: red}\n",                     # unknown key
        "connections:\n- {id: c, calls: x.add_edge, source: {arg: 0}}\n",     # no target
    ):
        with pytest.raises(ValidationError):
            parse_mapping(bad)


def test_mapping_round_trip_keeps_zero_arguments(tmp_path):
    mapping = parse_mapping(GRAPH_MAPPING)
    save_mapping(tmp_path, mapping)
    assert load_mapping(tmp_path) == mapping
    assert "arg: 0" in dump_mapping(mapping)


def test_invalid_mapping_file_is_reported_not_raised(tmp_path):
    project = _project(tmp_path, {"app.py": "X = 1\n", ".architecture/mapping.yaml": "agents: [{id: a}]\n"})
    model = inspect_project(project, [RulesAdapter()]).model
    assert [u.kind for u in model.unresolved] == ["mapping_invalid"]
