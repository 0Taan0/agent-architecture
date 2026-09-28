import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { AdapterStatus } from "./components/AdapterStatus";
import { Canvas } from "./components/Canvas";
import { InspectorPanel } from "./components/InspectorPanel";
import { UnresolvedList } from "./components/UnresolvedList";
import type { DriftReport, InspectionReport, Project } from "./types";

export default function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [current, setCurrent] = useState<Project | null>(null);
  const [report, setReport] = useState<InspectionReport | null>(null);
  const [drift, setDrift] = useState<DriftReport | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { api.projects().then(setProjects).catch((e: Error) => setError(e.message)); }, []);

  const inspect = useCallback(async (p: Project) => {
    setBusy(true); setError(null); setDrift(null); setSelected(null);
    try { setReport(await api.inspect(p.id)); } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }, []);

  const open = async (p: Project) => { setCurrent(p); await inspect(p); };

  const register = async () => {
    if (!path.trim()) return;
    setError(null);
    try {
      const p = await api.register(path.trim());
      setProjects((ps) => [...ps.filter((x) => x.id !== p.id), p]);
      setPath("");
      await open(p);
    } catch (e) { setError((e as Error).message); }
  };

  const loadDrift = async () => {
    if (!current) return;
    try { setDrift(await api.drift(current.id)); } catch (e) { setError((e as Error).message); }
  };

  const model = report?.model ?? null;
  return (
    <div className="app">
      <header className="topbar">
        <h1>agent-architect</h1>
        {current && <span className="mono muted">{current.path}</span>}
        <span className="spacer" />
        {current && <button onClick={() => inspect(current)} disabled={busy}>{busy ? "Inspecting…" : "Inspect again"}</button>}
        <span className="status">{model ? `${model.agents.length} agents · ${model.connections.length} connections · ${model.unresolved.length} unresolved` : "read-only map"}</span>
      </header>

      <nav className="rail">
        <div className="section">
          <h2>Projects</h2>
          <ul className="list">
            {projects.map((p) => (
              <li key={p.id}><button onClick={() => open(p)} style={{ width: "100%", textAlign: "left", fontWeight: current?.id === p.id ? 600 : 400 }}>{p.name}</button></li>
            ))}
            {projects.length === 0 && <li className="muted">No projects yet. Add a folder below.</li>}
          </ul>
          <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
            <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/path/to/project" onKeyDown={(e) => e.key === "Enter" && register()} style={{ flex: 1, minWidth: 0 }} />
            <button onClick={register}>Add</button>
          </div>
        </div>
        {report && <AdapterStatus adapters={report.adapters} />}
        {error && <p className="error">{error}</p>}
      </nav>

      {model ? (
        <Canvas model={model} selected={selected} onSelect={setSelected} />
      ) : (
        <div className="canvas"><div className="empty" style={{ padding: 32 }}>{busy ? "Reading the code…" : "Open a project to see its architecture. Nothing is written to the project."}</div></div>
      )}

      {model && current ? (
        <InspectorPanel projectId={current.id} model={model} selected={selected} />
      ) : (
        <aside className="inspector" />
      )}

      {model && <UnresolvedList unresolved={model.unresolved} drift={drift} onLoadDrift={loadDrift} />}
    </div>
  );
}
