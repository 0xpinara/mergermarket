import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { BadgeCheck, Play } from "lucide-react";
import { ApiError, api } from "../../lib/api";
import { useAppState } from "../../lib/store";
import { money, num, when } from "../../lib/format";
import { ErrorBox, Loading, SevChip, StatusPill, useToast } from "../ui";
import { Code, useInvalidateInteg } from "./shared";
import { Gates } from "./ExportView";

function Receipt({ dealId, enabled }: { dealId: string; enabled: boolean }) {
  const q = useQuery({
    queryKey: ["integ", dealId, "receipt"], queryFn: () => api.integ.receipt(dealId),
    retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2, enabled,
  });
  if (!q.data) return null;
  const r = q.data.receipt;
  const line = (a: string, b: string) => <div className="line"><span>{a}</span><span>{b}</span></div>;
  return (
    <div className="receipt release">
      <div className="ttl">MergeProof · release receipt{r.deal.synthetic ? " · synthetic" : ""}</div>
      {line("Deal", r.deal.title.slice(0, 34))}
      {line("Issued", `${when(r.issued_at)} · ${r.issued_by}`)}
      <div className="rule" />
      {line("Template", `${r.template.id} ${r.template.version}`)}
      {line("Plan", `${r.plan.hash.slice(0, 12)} v${r.plan.version}`)}
      {line("Dry run", `${r.dry_run.id} · ${num(r.dry_run.rows.accepted)} rows`)}
      {line("Signed off", `${r.signoff.reviewer} (${r.signoff.kind})`)}
      {line("Package", r.export.manifest_sha256.slice(0, 16) + "…")}
      {line("Verified run", r.verification.run_id)}
      {line("Findings", `${r.verification.findings.total} · ${Object.entries(r.verification.findings.by_status).map(([k, v]) => `${v} ${k}`).join(", ")}`)}
      <div className="rule" />
      {line("Gates", `${r.gates.filter((g) => g.status === "pass").length}/${r.gates.length} pass`)}
      <div className="line total"><span>sha256</span><span>{q.data.sha256.slice(0, 20)}…</span></div>
      <div className={`verdict ${q.data.verifies ? "v-ok" : "v-rent"}`}>
        {q.data.verifies ? "✓ Stored receipt matches its hash." : "✕ Stored receipt no longer matches its hash — it was altered."}
      </div>
      <ul className="limits">{r.limits.map((l) => <li key={l}>{l}</li>)}</ul>
    </div>
  );
}

export function VerifyView({ dealId, hasReceipt }: { dealId: string; hasReceipt: boolean }) {
  const { reviewer } = useAppState();
  const toast = useToast();
  const invalidate = useInvalidateInteg(dealId);
  const q = useQuery({ queryKey: ["integ", dealId, "verify"], queryFn: () => api.integ.verify(dealId) });
  const audit = useMutation({
    mutationFn: () => api.runAudit(dealId),
    onSuccess: () => { toast("Post-import verification complete"); invalidate(); },
    onError: (e: Error) => toast(e.message, "err"),
  });
  const issue = useMutation({
    mutationFn: () => api.integ.issueReceipt(dealId, reviewer),
    onSuccess: () => { toast("Release receipt issued"); invalidate(); },
    onError: (e: Error) => toast(e.message, "err"),
  });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.refetch()} />;
  const { comparison: c, readiness } = q.data!;
  const vg = readiness.gates.filter((g) => g.phase === "verify");
  const g9 = vg.find((g) => g.id === "G9");
  return (
    <div className="stack">
      <div className="sim-head">
        <p className="muted">
          After the import files are loaded, export the target system’s data (Audit Workspace → consolidated side) and verify it against
          the legacy exports. The dry run can only show what the <i>plan</i> would do; this shows what the <i>load</i> actually did.
        </p>
        <button className="btn primary lg" onClick={() => audit.mutate()} disabled={audit.isPending || !readiness.export_ready}
                title={readiness.export_ready ? "" : "Sign off and export first"}>
          <Play /> {audit.isPending ? "Verifying…" : "Run post-import verification"}
        </button>
      </div>
      {g9 && g9.status !== "pass" && <div className="banner info"><span className="ico">·</span><span>{g9.detail}</span></div>}

      {c.available && c.counts && (
        <>
          <div className="kpis three">
            <div className="kpi"><div className="k">Predicted by the dry run</div><div className="v">{c.counts.predicted}</div>
              <div className="s">{c.counts.both} of them observed after the load</div></div>
            <div className="kpi"><div className="k">Observed only · import-time</div><div className={`v${c.counts.observed_only ? " coral" : ""}`}>{c.counts.observed_only}</div>
              <div className="s">introduced by the load itself — only verification catches these</div></div>
            <div className="kpi"><div className="k">Predicted, not observed</div><div className="v">{c.counts.predicted_only}</div>
              <div className="s">fixed during the load, or never happened</div></div>
          </div>
          <section className="card">
            <div className="card-head"><h3>Three-way tie-out</h3><span className="muted small">legacy → simulated → actual</span></div>
            <div className="table-wrap">
              <table className="map-table tie3">
                <thead><tr><th>Check</th><th>Legacy</th><th></th><th>Simulated</th><th></th><th>Actual (after load)</th></tr></thead>
                <tbody>
                  {c.tieouts!.map((t) => {
                    const f = (v: number | null) => (v === null ? "—" : t.kind === "money" ? money(v) : num(v));
                    return (
                      <tr key={t.id}>
                        <td>{t.label}</td>
                        <td className="mono">{f(t.legacy)}</td>
                        <td className={`mono eq ${t.simulated_ties ? "ok" : "bad"}`} aria-label={t.simulated_ties ? "ties" : "differs"}>{t.simulated_ties ? "═" : "≠"}</td>
                        <td className="mono">{f(t.simulated)}</td>
                        <td className={`mono eq ${t.actual_ties ? "ok" : "bad"}`} aria-label={t.actual_ties ? "ties" : "differs"}>{t.actual_ties ? "═" : "≠"}</td>
                        <td className={`mono${t.actual_ties ? "" : " coral"}`}>{f(t.actual)}
                          {!t.actual_ties && t.actual !== null && <span className="small"> ({t.kind === "money" ? money(t.actual - t.legacy, { signed: true }) : (t.actual - t.legacy > 0 ? "+" : "") + num(t.actual - t.legacy)})</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
          {c.observed_only!.length > 0 && (
            <section className="card">
              <div className="card-head"><h3>Import-time defects</h3><span className="muted small">{c.observed_only!.length} observed, not predicted</span></div>
              <p className="muted small card-note">{c.note}</p>
              <ul className="predicted card-body">
                {c.observed_only!.map((f) => (
                  <li key={f.finding_id}>
                    <SevChip severity={f.severity} /> <span className="mono small">{f.rule_id}</span>{" "}
                    <Link to={`/deals/${dealId}/findings?f=${f.finding_id}`}>{f.title}</Link>
                    <span className="muted small"> · {f.subject}</span>
                    {f.rent_risk_cents > 0 && <span className="mono coral small"> −{money(f.rent_risk_cents)}/mo</span>}
                    {f.deposit_cents > 0 && <span className="mono amber small"> {money(f.deposit_cents)} deposit</span>}
                    <span className="grow" /> <StatusPill status={f.status} />
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}

      <div className="grid split">
        <section className="card">
          <div className="card-head"><h3>Release gates · after the load</h3></div>
          <div className="card-body">
            <Gates gates={vg} />
            <div className="btn-row" style={{ marginTop: 14 }}>
              <button className="btn primary" disabled={!readiness.release_ready || issue.isPending || !reviewer.trim()} onClick={() => issue.mutate()}>
                <BadgeCheck /> Issue release receipt
              </button>
              <Link className="btn ghost" to={`/deals/${dealId}/findings`}>Review findings</Link>
            </div>
            <p className="muted small" style={{ marginTop: 8 }}>
              A receipt records which checks ran and passed on which inputs (hashes of the plan, dry run, package and verification run).
              It is not an audit opinion. Dry run <Code>{c.dry_run_id ?? "—"}</Code> · verification <Code>{c.run_id ?? "—"}</Code>.
            </p>
          </div>
        </section>
        <Receipt dealId={dealId} enabled={hasReceipt} />
      </div>
    </div>
  );
}
