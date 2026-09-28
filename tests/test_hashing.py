from core.models import AgentNode, ArchitectureModel, Provenance
from core.ownership import architecture_hash, content_hash


def _model(desc=None, adapter="x", order=("a", "b")):
    agents = {"a": AgentNode(id="agent_a", name="A", provenance=Provenance.DECLARED,
                             source_adapter=adapter, description=desc),
              "b": AgentNode(id="agent_b", name="B", provenance=Provenance.DECLARED)}
    return ArchitectureModel(name="m", agents=[agents[k] for k in order])


def test_hash_is_stable_across_order_adapter_and_description():
    h = architecture_hash(_model())
    assert h == architecture_hash(_model(order=("b", "a")))
    assert h == architecture_hash(_model(adapter="other"))
    assert h == architecture_hash(_model(desc="comment"))
    assert h.startswith("sha256:")


def test_hash_changes_on_structural_change():
    m = _model()
    m.agents[0].tools = ["web"]
    assert architecture_hash(m) != architecture_hash(_model())


def test_content_hash_bytes_and_str_agree():
    assert content_hash("abc") == content_hash(b"abc")
