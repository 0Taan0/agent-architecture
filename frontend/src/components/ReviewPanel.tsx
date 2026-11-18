// Review of the mapping rules: the user accepts or rejects what discovery proposed.
//
// NOT WIRED IN YET. Nothing imports this file, so the running app is unchanged.
// It is self-contained on purpose (own types, own requests) so it can be judged alone.
// To use it:
//   1. App.tsx: render <ReviewPanel projectId={current.id} onChanged={() => inspect(current)} />
//      for example as a third tab of the drawer next to "Unresolved" and "Drift".
//   2. Optional: pass onHighlight to mark a rule's elements on the canvas, and
//      onOpenEvidence to open a file at a line in the code view.
//   3. Then move the types to types.ts and the two requests to api.ts.
//
// Backend: GET /api/projects/{id}/mapping, POST /api/projects/{id}/mapping/status
import { useCallback, useEffect, useState } from "react";

type Status = "proposed" | "accepted" | "rejected";

interface Issue {
  kind: string;
  message: string;
  rule?: string | null;
  severity: "error" | "warning";
}

interface RuleReview {
  id: string;
  kind: "agent" | "connection" | "unresolved";
  status: Status;
  text: string;
  rationale?: string | null;
  evidence: string[];
  yields: string[];
  element_ids: string[];
  matches: number;
  issues: Issue[];
}

interface ReviewState {
  exists: boolean;
  code_changed: boolean;
  rules: RuleReview[];
  issues: Issue[];
  descriptions: Record<string, string>;
}

interface Props {
  projectId: string;
  // Called after a decision was saved, so the caller can inspect again
  onChanged?: () => void;
  // Called with the ids of the agents and connections a rule yields
  onHighlight?: (elementIds: string[]) => void;
  onOpenEvidence?: (file: string, line: number) => void;
}

// Same border language as the canvas: accepted = declared, proposed = inferred
const CHIP: Record<Status, string> = {
  accepted: "prov declared",
  proposed: "prov inferred",
  rejected: "prov unknown",
};

async function request(url: string, init?: RequestInit): Promise<ReviewState> {
  const res = await fetch(url, { headers: { "Content-Type": "application/json" }, ...init });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`);
  }
  return res.json() as Promise<ReviewState>;
}

const hasError = (rule: RuleReview) => rule.issues.some((i) => i.severity === "error");

export function ReviewPanel({ projectId, onChanged, onHighlight, onOpenEvidence }: Props) {
  const [state, setState] = useState<ReviewState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<string | null>(null);

  const url = `/api/projects/${projectId}/mapping`;

  useEffect(() => {
    setState(null);
    setError(null);
    request(url).then(setState).catch((e: Error) => setError(e.message));
  }, [url]);

  const decide = useCallback(async (ruleIds: string[], status: Status) => {
    if (ruleIds.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      setState(await request(`${url}/status`, {
        method: "POST",
        body: JSON.stringify({ rule_ids: ruleIds, status }),
      }));
      onChanged?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [url, onChanged]);

  const select = (rule: RuleReview) => {
    setActive(rule.id);
    onHighlight?.(rule.element_ids);
  };

  if (error && !state) return <p className="error">{error}</p>;
  if (!state) return <div className="empty">Reading the mapping rules…</div>;
  if (!state.exists) {
    return <div className="empty">No mapping rules yet. Run <code>agent-architect discover</code> for this project.</div>;
  }

  const waiting = state.rules.filter((r) => r.status === "proposed");
  // Safe to accept in one go: waiting, and the validator has nothing to say about them
  const clean = waiting.filter((r) => r.issues.length === 0).map((r) => r.id);

  return (
    <section>
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "8px 16px" }}>
        <strong>{waiting.length === 0 ? "All rules are decided" : `${waiting.length} of ${state.rules.length} rules wait for review`}</strong>
        <span style={{ flex: 1 }} />
        <button disabled={busy || clean.length === 0} onClick={() => decide(clean, "accepted")}>
          Accept all without issues ({clean.length})
        </button>
      </div>

      {state.code_changed && (
        <p className="error" style={{ padding: "0 16px" }}>
          The code changed since these rules were discovered. Run discover again.
        </p>
      )}
      {state.issues.map((issue, i) => (
        <p key={i} className="error" style={{ padding: "0 16px" }}>{issue.message}</p>
      ))}
      {error && <p className="error" style={{ padding: "0 16px" }}>{error}</p>}

      <table>
        <thead>
          <tr><th>Status</th><th>Rule</th><th>Yields</th><th>Evidence</th><th /></tr>
        </thead>
        <tbody>
          {state.rules.map((rule) => (
            <tr key={rule.id} onClick={() => select(rule)}
                style={{ cursor: "pointer", background: active === rule.id ? "var(--bg)" : undefined }}>
              <td><span className={CHIP[rule.status]}>{rule.status}</span></td>
              <td>
                <div><strong>{rule.id}</strong> <span className="muted">· {rule.kind}</span></div>
                <div className="mono">{rule.text}</div>
                {rule.rationale && <div className="muted">{rule.rationale}</div>}
                {rule.issues.map((issue, i) => (
                  <div key={i} className={issue.severity === "error" ? "error" : "sev structural"}>
                    {issue.severity}: {issue.message}
                  </div>
                ))}
              </td>
              <td>
                {rule.yields.length === 0
                  ? <span className="muted">{rule.matches} matches</span>
                  : rule.yields.map((label) => <div key={label}>{label}</div>)}
              </td>
              <td className="mono">
                {rule.evidence.map((ref) => {
                  const cut = ref.lastIndexOf(":");
                  const file = ref.slice(0, cut);
                  const line = Number(ref.slice(cut + 1));
                  return onOpenEvidence ? (
                    <div key={ref}>
                      <button onClick={(e) => { e.stopPropagation(); onOpenEvidence(file, line); }}
                              style={{ border: 0, padding: 0, background: "none", textDecoration: "underline" }}>
                        {ref}
                      </button>
                    </div>
                  ) : <div key={ref}>{ref}</div>;
                })}
              </td>
              <td style={{ whiteSpace: "nowrap" }}>
                <button disabled={busy || rule.status === "accepted" || hasError(rule)}
                        title={hasError(rule) ? "A rule with an error cannot be accepted" : undefined}
                        onClick={(e) => { e.stopPropagation(); decide([rule.id], "accepted"); }}>
                  Accept
                </button>{" "}
                <button disabled={busy || rule.status === "rejected"}
                        onClick={(e) => { e.stopPropagation(); decide([rule.id], "rejected"); }}>
                  Reject
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {Object.keys(state.descriptions).length > 0 && (
        <div style={{ padding: "8px 16px" }}>
          <h2>Descriptions written by the model</h2>
          <dl className="kv">
            {Object.entries(state.descriptions).map(([name, text]) => (
              <div key={name} style={{ display: "contents" }}><dt>{name}</dt><dd>{text}</dd></div>
            ))}
          </dl>
        </div>
      )}
    </section>
  );
}
