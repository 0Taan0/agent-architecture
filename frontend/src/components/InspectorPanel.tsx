import type { ArchitectureModel } from "../types";
import { CodeView } from "./CodeView";

interface Props { projectId: string; model: ArchitectureModel; selected: string | null }

export function InspectorPanel({ projectId, model, selected }: Props) {
  const agent = model.agents.find((a) => a.id === selected);
  if (!agent) {
    return (
      <aside className="inspector">
        <h2>Inspector</h2>
        <p className="muted">Select an agent on the map to see its model, schemas, tools and the code it was read from.</p>
        <div className="section">
          <h2>Schemas</h2>
          <ul className="list">
            {model.schemas.map((s) => (
              <li key={s.id}>
                <span className={`prov ${s.provenance}`}>{s.provenance}</span> <strong>{s.name}</strong>
                <div className="mono muted">{s.fields.map((f) => `${f.name}: ${f.type}${f.optional ? "?" : ""}`).join(", ") || "no fields"}</div>
              </li>
            ))}
            {model.schemas.length === 0 && <li className="muted">No schemas found.</li>}
          </ul>
        </div>
      </aside>
    );
  }
  const schema = (id?: string | null) => (id ? model.schemas.find((s) => s.id === id) : undefined);
  const inS = schema(agent.input_schema), outS = schema(agent.output_schema);
  const incoming = model.connections.filter((c) => c.target === agent.id);
  const outgoing = model.connections.filter((c) => c.source === agent.id);
  const name = (id: string) => model.agents.find((a) => a.id === id)?.name ?? id.replace(/^__|__$/g, "");
  return (
    <aside className="inspector">
      <h1>{agent.name}</h1>
      <p><span className={`prov ${agent.provenance}`}>{agent.provenance}</span> <span className="muted">via {agent.source_adapter}</span></p>
      {agent.description && <p className="muted">{agent.description}</p>}
      <div className="section">
        <dl className="kv">
          <dt>Id</dt><dd className="mono">{agent.id}</dd>
          <dt>Model</dt><dd>{agent.model?.model ?? <span className="muted">not in code</span>}{agent.model?.temperature != null && <span className="muted"> · temp {agent.model.temperature}</span>}</dd>
          <dt>Input</dt><dd>{inS ? inS.name : <span className="muted">unknown</span>}</dd>
          <dt>Output</dt><dd>{outS ? outS.name : <span className="muted">unknown</span>}</dd>
          <dt>Tools</dt><dd>{agent.tools.length ? agent.tools.join(", ") : <span className="muted">none</span>}</dd>
          <dt>Receives from</dt><dd>{incoming.length ? incoming.map((c) => name(c.source)).join(", ") : <span className="muted">–</span>}</dd>
          <dt>Sends to</dt><dd>{outgoing.length ? outgoing.map((c) => `${name(c.target)}${c.condition ? ` (${c.condition})` : ""}`).join(", ") : <span className="muted">–</span>}</dd>
        </dl>
      </div>
      {[inS, outS].filter(Boolean).map((s) => (
        <div className="section" key={s!.id}>
          <h2>{s === inS ? "Input schema" : "Output schema"}: {s!.name}</h2>
          <ul className="list">
            {s!.fields.map((f) => (
              <li key={f.name} className="mono">{f.name}: {f.type}{f.optional ? " (optional)" : ""}</li>
            ))}
          </ul>
        </div>
      ))}
      <div className="section">
        <h2>Code</h2>
        {agent.code_ref ? (
          <>
            <div className="mono">{agent.code_ref.file}{agent.code_ref.line ? `:${agent.code_ref.line}` : ""}{agent.code_ref.symbol ? ` · ${agent.code_ref.symbol}` : ""}</div>
            <CodeView projectId={projectId} ref_={agent.code_ref} />
          </>
        ) : (
          <p className="muted">No code location. This node exists only in the architecture.</p>
        )}
      </div>
    </aside>
  );
}
