from core.models import (
    AgentNode,
    ArchitectureModel,
    Connection,
    Provenance,
    SchemaDef,
    SchemaField,
    agent_id,
    connection_id,
    from_yaml,
    to_yaml,
)


def test_stable_ids_are_deterministic():
    assert agent_id("agents/a.py", "A") == agent_id("agents/a.py", "A")
    assert agent_id("agents/a.py", "A") != agent_id("agents/b.py", "A")
    assert agent_id("agents\\a.py", "A") == agent_id("agents/a.py", "A")   # windows paths
    assert connection_id("x", "y") != connection_id("x", "y", "cond")


def test_yaml_roundtrip_is_lossless_and_sorted():
    m = ArchitectureModel(name="t", agents=[
        AgentNode(id="agent_b", name="B", provenance=Provenance.DECLARED, source_adapter="x"),
        AgentNode(id="agent_a", name="A", tools=["t1"], provenance=Provenance.CONFIRMED,
                  source_adapter="x", input_schema="schema_1"),
    ], schemas=[SchemaDef(id="schema_1", name="S", fields=[SchemaField(name="f", type="int")],
                          provenance=Provenance.CONFIRMED)],
        connections=[Connection(id="conn_1", source="agent_a", target="agent_b",
                                provenance=Provenance.INFERRED, source_adapter="x")])
    text = to_yaml(m)
    back = from_yaml(text)
    assert back == m.sorted()
    assert to_yaml(back) == text
    assert text.index("agent_a") < text.index("agent_b")
