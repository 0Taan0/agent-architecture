import type { AdapterStatus as Status } from "../types";

export function AdapterStatus({ adapters }: { adapters: Status[] }) {
  return (
    <div className="section">
      <h2>Adapters</h2>
      {adapters.map((a) => (
        <div className={`adapter ${a.confidence}`} key={a.name}>
          <span>{a.name}</span>
          <span className="conf">{a.confidence}{a.ran ? "" : " · not run"}</span>
          {a.ran && (
            <span className="contrib">
              {a.agents} agents · {a.connections} connections · {a.schemas} schemas
              {a.unresolved > 0 && ` · ${a.unresolved} unresolved`}
              {a.notes.map((n) => <div key={n}>{n}</div>)}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
