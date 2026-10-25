import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Play, RotateCw } from "lucide-react";
import { api, ApiError, type Deal, type Stage, type Summary } from "../lib/api";
import { money, pct } from "../lib/format";
import { usePrefersReducedMotion } from "../lib/store";

const STAGES = ["Preparing exports", "Mapping fields", "Matching entities", "Testing invariants", "Building evidence"];

export function useRunAudit(dealId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.runAudit(dealId),
    onSuccess: () => {
      for (const k of ["summary", "deal", "findings", "unitmap", "reviews", "report"]) qc.invalidateQueries({ queryKey: [k, dealId] });
      qc.invalidateQueries({ queryKey: ["deals"] });
      qc.invalidateQueries({ queryKey: ["finding"] });
    },
  });
}

export function RunAudit({ deal, compact = false }: { deal: Deal; compact?: boolean }) {
  const run = useRunAudit(deal.id);
  const reduced = usePrefersReducedMotion();
  const [shown, setShown] = useState(0);
  const [result, setResult] = useState<{ stages: Stage[]; summary: Summary } | null>(null);

  useEffect(() => {
    if (!result) return;
    if (reduced) {
      setShown(result.stages.length + 1);
      return;
    }
    setShown(0);
    let i = 0;
    const t = setInterval(() => {
      i += 1;
      setShown(i);
      if (i > result.stages.length) clearInterval(t);
    }, 240);
    return () => clearInterval(t);
  }, [result, reduced]);

  const start = () => {
    setResult(null);
    setShown(0);
    run.mutate(undefined, { onSuccess: (r) => setResult({ stages: r.run.stages, summary: r.summary }) });
  };

  const state = deal.status.state;
  const blocked = state === "draft" || state === "needs_input";
  const hasRun = !!deal.status.last_run_at;
  const err = run.error as ApiError | null;
  const done = result && shown > result.stages.length;
  const k = result?.summary.kpis;
  const c = result?.summary.continuity;

  return (
    <div className="term on-ink" aria-live="polite">
      <div className="term-bar">
        <i /> <i /> <i />
        <span>mergeproof — continuity audit</span>
      </div>
      <div className="term-body">
        <div className="prompt">
          <b>$</b> mergeproof audit <span style={{ color: "var(--ink-200)" }}>--deal “{deal.title}”</span>
        </div>
        {(run.isPending || result) &&
          STAGES.map((name, i) => {
            const st = result?.stages[i];
            const visible = result ? i < shown : false;
            const active = run.isPending ? i === 0 : result && i === shown;
            const cls = visible ? "done" : active ? "" : "wait";
            return (
              <div className={`term-line ${cls}`} key={name}>
                <span className="st" aria-hidden="true">{visible ? "✓" : active ? <span className="spin" /> : "·"}</span>
                <span className="nm">{name}</span>
                <span className="dt">{visible && st ? st.detail : active ? "working…" : ""}</span>
                <span className="ms">{visible && st ? `${st.ms.toFixed(0)}ms` : ""}</span>
              </div>
            );
          })}
        {err && (
          <div style={{ marginTop: 6 }} role="alert">
            <div className="bad">✕ {err.message}</div>
            {err.problems?.map((p) => (
              <div key={p} className="bad" style={{ paddingLeft: 18 }}>
                → {p}
              </div>
            ))}
          </div>
        )}
        {done && k && c && (
          <div style={{ marginTop: 8 }}>
            <div className="ok">
              ✓ audit complete · {k.findings_total} findings · {k.critical_open} critical
            </div>
            <div style={{ color: "var(--ink-200)" }}>
              → continuity {pct(c.score)} ({c.accounted}/{c.active_leases} active leases accounted for)
            </div>
            <div style={{ color: "var(--ink-200)" }}>
              → est. monthly rent at risk <span className="bad">{money(k.rent_at_risk_open)}</span> (requires review) ·
              deposits <span style={{ color: "var(--amber)" }}>{money(k.deposit_open)}</span> tracked separately
            </div>
            {result?.summary.ai && (
              <div style={{ color: "var(--ink-300)" }}>
                {result.summary.ai.available
                  ? <>→ AI assist (local {result.summary.ai.model}): {result.summary.ai.suggestions} suggestion{result.summary.ai.suggestions === 1 ? "" : "s"} for review · {result.summary.ai.decisions_by_ai} decisions made by AI</>
                  : <>→ AI assist off ({result.summary.ai.reason}) · rules only</>}
              </div>
            )}
          </div>
        )}
        {!run.isPending && !result && !err && (
          <div style={{ color: "var(--ink-400)", marginTop: 4 }}>
            {blocked
              ? "# waiting for exports on both sides (and required field mappings)"
              : hasRun
                ? `# last run ${new Date(deal.status.last_run_at!).toLocaleString()} — rerun anytime; reviewed decisions are kept`
                : "# ready: 5 stages — prepare → map → match → test → evidence"}
            {!blocked && <span className="cursor" style={{ marginLeft: 6 }} />}
          </div>
        )}
        <div className="btn-row" style={{ marginTop: 14 }}>
          <button className="btn primary" onClick={start} disabled={blocked || run.isPending}>
            {hasRun ? <RotateCw /> : <Play />}
            {run.isPending ? "Running audit…" : hasRun ? "Rerun continuity audit" : "Run continuity audit"}
          </button>
          {done && (
            <>
              <Link className="btn ghost" to={`/deals/${deal.id}/findings`}>
                Open findings →
              </Link>
              {!compact && (
                <Link className="btn ghost" to="/">
                  Command Center
                </Link>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
