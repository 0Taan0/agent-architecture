
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_ARCHITECT_HOME", str(tmp_path / "home"))
    import backend.routes.projects as routes
    routes._registry = None
    from backend.app import app
    return TestClient(app)


def test_register_inspect_file_and_traversal_guard(client, prozess_mas):
    r = client.post("/api/projects", json={"path": str(prozess_mas.root)})
    assert r.status_code == 200
    pid = r.json()["id"]
    assert client.get("/api/projects").json()[0]["id"] == pid

    rep = client.post(f"/api/projects/{pid}/inspect").json()
    assert {a["name"] for a in rep["model"]["agents"]} >= {"CriticAgent"}
    assert any(a["name"] == "project_local" and a["ran"] for a in rep["adapters"])

    arch = client.get(f"/api/projects/{pid}/architecture").json()
    assert arch["stored"] is None and "positions" in arch["layout"]

    f = client.get(f"/api/projects/{pid}/file", params={"path": "agents/critic.py"})
    assert f.status_code == 200 and "class CriticAgent" in f.json()["content"]
    assert client.get(f"/api/projects/{pid}/file", params={"path": "../../pyproject.toml"}).status_code == 403
    assert client.get(f"/api/projects/{pid}/file", params={"path": "/etc/passwd"}).status_code == 403

    adapters = client.get(f"/api/projects/{pid}/adapters").json()
    assert {a["name"] for a in adapters} == {"declarations", "project_local", "langgraph", "conventions"}

    drift = client.get(f"/api/projects/{pid}/drift").json()     # no architecture.yaml -> import view
    assert any(i["kind"] == "agent_added_in_code" for i in drift["items"])
    assert client.post(f"/api/projects/{pid}/generate/plan").status_code == 400
