import { useState } from "react";
import type { DriftReport, UnresolvedItem } from "../types";

interface Props { unresolved: UnresolvedItem[]; drift: DriftReport | null; onLoadDrift: () => void }

/** Visible, never hidden: the map names its own gaps (Spec 7.5 / 8.3). */
export function UnresolvedList({ unresolved, drift, onLoadDrift }: Props) {
  const [tab, setTab] = useState<"unresolved" | "drift">("unresolved");
  const visibleDrift = drift?.items.filter((i) => i.severity !== "cosmetic") ?? [];
  return (
    <section className="drawer">
      <header>
        <button className={tab === "unresolved" ? "active" : ""} onClick={() => setTab("unresolved")}>
          Unresolved ({unresolved.length})
        </button>
        <button className={tab === "drift" ? "active" : ""} onClick={() => { setTab("drift"); if (!drift) onLoadDrift(); }}>
          Drift{drift ? ` (${visibleDrift.length})` : ""}
        </button>
      </header>
      {tab === "unresolved" && (unresolved.length === 0 ? (
        <div className="empty">Nothing unresolved. Everything on the map was read from the code or declared.</div>
      ) : (
        <table>
          <thead><tr><th>Kind</th><th>Location</th><th>Detail</th><th>Adapter</th></tr></thead>
          <tbody>
            {unresolved.map((u, i) => (
              <tr key={i}><td className="mono">{u.kind}</td><td className="mono">{u.location ?? ""}</td><td>{u.message ?? ""}</td><td className="muted">{u.source_adapter}</td></tr>
            ))}
          </tbody>
        </table>
      ))}
      {tab === "drift" && (!drift ? (
        <div className="empty">Comparing architecture.yaml with the code…</div>
      ) : visibleDrift.length === 0 ? (
        <div className="empty">No drift. architecture.yaml and the code agree.</div>
      ) : (
        <table>
          <thead><tr><th>Severity</th><th>Kind</th><th>Subject</th><th>Architecture</th><th>Code</th><th>Where</th></tr></thead>
          <tbody>
            {visibleDrift.map((d, i) => (
              <tr key={i}>
                <td className={`sev ${d.severity}`}>{d.severity}</td>
                <td className="mono">{d.kind}</td>
                <td>{d.subject}{d.field ? <span className="muted"> · {d.field}</span> : null}</td>
                <td className="mono">{d.architecture ?? ""}</td>
                <td className="mono">{d.code ?? ""}</td>
                <td className="mono muted">{d.location ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ))}
    </section>
  );
}
