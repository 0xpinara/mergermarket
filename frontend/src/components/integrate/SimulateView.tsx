import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Play } from "lucide-react";
import { ApiError, api, type Blast, type DryRun, type Plan } from "../../lib/api";
import { useAppState } from "../../lib/store";
import { money, num, plural, when } from "../../lib/format";
import { Empty, ErrorBox, Info, Loading, SevChip, useToast } from "../ui";
import { Code, Hazard, useInvalidateInteg } from "./shared";

function BlastTree({ b }: { b: Blast }) {
  const rows = Object.entries(b.rows);
  return (
    <div className="blast">
      <div className="bh">
        <span className="x" aria-hidden="true">✕</span>
        <span className="mono b">{b.check}</span>
        <span className="mono">{b.table}.{b.column}</span>
        <span className="muted">— {b.label.toLowerCase()}</span>
      </div>
      <ul className="tree mono">
        {rows.map(([t, n], k) => (
          <li key={t}>
            <span className="br" aria-hidden="true">{k === rows.length - 1 ? "└──" : "├──"}</span>
            <span>{num(n)} {t.replace("_", " ")} rejected{t !== b.table ? " (they point at rejected rows)" : ""}</span>
          </li>
        ))}
      </ul>
      <div className="impact">
        {b.active_leases > 0 && <span><b>{num(b.active_leases)}</b> active leases · <b className="coral">{money(b.rent_cents)}/mo</b> rent would not bill</span>}
        {b.deposit_cents > 0 && <span><b className="amber">{money(b.deposit_cents)}</b> deposits unrecorded (liability)</span>}
        {b.open_work_orders > 0 && <span><b>{num(b.open_work_orders)}</b> open work orders lost</span>}
      </div>
    </div>
  );
}

function Preview({ dealId, dr }: { dealId: string; dr: DryRun }) {
  const tables = dr.tables.filter((t) => t.source_rows);
  const [table, setTable] = useState(tables.find((t) => t.rejected)?.table ?? tables[0]?.table ?? "");
  const [rejectedPick, setRejected] = useState(false);
  const cur0 = tables.find((t) => t.table === table);
  const rejected = rejectedPick || (!!cur0 && cur0.accepted === 0 && cur0.rejected > 0);
  const q = useQuery({
    queryKey: ["integ", dealId, "preview", table, rejected, dr.id],
    queryFn: () => api.integ.preview(dealId, table, 0, 12, rejected),
    enabled: !!table,
  });
  const p = q.data;
  const cur = tables.find((t) => t.table === table);
  return (
    <section className="card">
      <div className="card-head">
        <h3>Simulated import rows</h3>
        <div className="seg" role="group" aria-label="Table">
          {tables.map((t) => (
            <button key={t.table} aria-pressed={t.table === table} onClick={() => setTable(t.table)}>
              {t.table.replace("_", " ")}{t.rejected ? <span className="c">✕{t.rejected}</span> : null}
            </button>
          ))}
        </div>
      </div>
      <div className="card-body">
        <div className="btn-row" style={{ marginBottom: 10 }}>
          <span className="mono small">{cur?.file}</span>
          <span className="muted small">{num(cur?.accepted ?? 0)} accepted · {num(cur?.rejected ?? 0)} rejected</span>
          <span className="grow" />
          <label className="small"><input type="checkbox" checked={rejected} disabled={!!cur && cur.accepted === 0} onChange={(e) => setRejected(e.target.checked)} /> show rejected rows</label>
        </div>
        {q.isLoading && <Loading />}
        {p && !rejected && p.rows && (
          <div className="table-wrap sim-wrap">
            <table className="map-table preview sim">
              <thead><tr><th className="rn">legacy row</th>{p.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>
                {p.rows.map((r, k) => {
                  const lin = p.lineage?.[k];
                  return (
                    <tr key={k}>
                      <td className="rn mono">{lin ? `${lin.file.replace(/\.csv$/, "")}:${lin.row}` : ""}</td>
                      {p.columns.map((c) => {
                        const l = lin?.columns[c];
                        return <td key={c} title={l ? `${lin!.file} row ${lin!.row} · ${l.sources.join(" + ") || "—"} · ${l.op}` : "not mapped"}>{r[c] || <span className="muted">·</span>}</td>;
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {p && rejected && (
          p.rejected?.length ? (
            <ul className="rejects mono small">
              {p.rejected.map((r) => (
                <li key={r.row}><span className="muted">row {r.row}</span> {r.problems.map((x) => `${x.check} ${x.column}${x.detail ? ` (${x.detail})` : ""}`).join("; ")}
                  {r.root.table !== table && <span className="muted"> ← caused by {r.root.check} {r.root.table}.{r.root.column}</span>}</li>
              ))}
            </ul>
          ) : <p className="muted small">No rejected rows in this table.</p>
        )}
        <p className="muted small" style={{ marginTop: 8 }}>Hover a cell for its lineage: legacy file, row, source column(s), transform.</p>
      </div>
    </section>
  );
}

export function SimulateView({ dealId, plan, hasDryRun }: { dealId: string; plan: Plan; hasDryRun: boolean }) {
  const { reviewer } = useAppState();
  const toast = useToast();
  const invalidate = useInvalidateInteg(dealId);
  const q = useQuery({
    queryKey: ["integ", dealId, "dry"], queryFn: () => api.integ.latestDryRun(dealId),
    retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2, enabled: hasDryRun,
  });
  const run = useMutation({
    mutationFn: () => api.integ.dryRun(dealId, reviewer || "Reviewer"),
    onSuccess: (d) => {
      toast(d.totals.rejected ? `Dry run: ${num(d.totals.rejected)} rows would be rejected` : "Dry run clean — nothing would be rejected");
      invalidate();
    },
    onError: (e: Error) => toast(e.message, "err"),
  });
  const none = q.error instanceof ApiError && q.error.status === 404;
  const dr = q.data;
  const left = plan.counts.total - plan.counts.decided;

  const head = (
    <div className="sim-head">
      <div>
        <p className="muted">
          Executes the plan against the legacy rows, checks every simulated row the way a strict importer would, then runs the
          same reconciliation engine on <i>legacy vs simulated</i>. Nothing is written anywhere.
        </p>
        {left > 0 && <p className="warnline"><span aria-hidden="true">!</span><span>{plural(left, "column")} not yet decided — the dry run uses the current suggestions as provisional.</span></p>}
        {plan.drift.changed && <p className="errline"><span aria-hidden="true">✕</span><span>The legacy exports changed. Rebase the plan before simulating.</span></p>}
      </div>
      <button className="btn primary lg" onClick={() => run.mutate()} disabled={run.isPending || plan.drift.changed}>
        <Play /> {run.isPending ? "Simulating…" : dr ? "Run dry run again" : "Run dry run"}
      </button>
    </div>
  );

  if (hasDryRun && q.isLoading) return <Loading />;
  if (q.error && !none) return <ErrorBox error={q.error} onRetry={() => q.refetch()} />;
  if (!dr) return (
    <div className="stack">
      {head}
      <Empty art={"  legacy ──▶ [ plan ] ──▶ ▒▒▒▒▒\n                         simulated"} title="No dry run yet">
        Run one to see row-level rejects, blast radius, tie-outs and the findings the import would cause.
      </Empty>
    </div>
  );
  const pk = dr.predicted_kpis;
  const stale = dr.plan_hash !== plan.plan_hash;
  const blocking = dr.preflight.filter((g) => g.blocking);
  return (
    <div className="stack">
      {head}
      <Hazard>{dr.banner}</Hazard>
      {stale && (
        <div className="banner"><span className="ico">↻</span><span>The plan changed after this dry run (v{dr.plan_version} → v{plan.version}).</span>
          <span className="grow">Gates use the latest dry run only when it matches the current plan hash.</span></div>
      )}
      <div className="kpis sim-kpis">
        <div className="kpi"><div className="k">Rows simulated</div><div className="v">{num(dr.totals.accepted)}<small>/ {num(dr.totals.source_rows)}</small></div>
          <div className="s">across {dr.tables.filter((t) => t.source_rows).length} import files</div></div>
        <div className="kpi"><div className="k">Rejected</div><div className={`v${dr.totals.rejected ? " coral" : ""}`}>{num(dr.totals.rejected)}</div>
          <div className="s">{dr.totals.blocking_checks} blocking check{dr.totals.blocking_checks === 1 ? "" : "s"} · {dr.totals.warnings} warnings</div></div>
        <div className="kpi"><div className="k">Predicted findings</div><div className="v">{num(pk.findings)}</div>
          <div className="s"><b>{pk.critical}</b> critical · <b>{pk.high}</b> high</div></div>
        <div className="kpi"><div className="k">Rent at risk if imported <Info>Deduplicated per lease, like the audit. A monthly estimate, not a loss. Deposits are separate.</Info></div>
          <div className={`v${pk.rent_at_risk_cents ? " coral" : ""}`}>{money(pk.rent_at_risk_cents)}<small>/mo</small></div>
          <div className="s">{money(pk.deposit_cents)} deposits · separate</div></div>
      </div>
      {dr.diff && (
        <p className="diffline mono">
          <span className="muted">vs dry run v{dr.diff.previous_version}:</span>{" "}
          rejected {num(dr.diff.rejected_rows.before)} → <b>{num(dr.diff.rejected_rows.after)}</b> ·
          predicted findings −{dr.diff.predicted.resolved} / +{dr.diff.predicted.new} ·
          rent at risk {money(dr.diff.rent_at_risk.before)} → <b>{money(dr.diff.rent_at_risk.after)}</b>
          {dr.diff.preflight.resolved.length > 0 && <> · resolved {dr.diff.preflight.resolved.map((k) => {
            const [c, t, col] = k.split("|");
            return `${c} ${t}.${col}`;
          }).join(", ")}</>}
        </p>
      )}

      {dr.blast_radius.length > 0 && (
        <section className="card">
          <div className="card-head"><h3>Blast radius</h3><span className="muted small">root cause → everything it takes down</span></div>
          <div className="card-body stack tight">{dr.blast_radius.map((b) => <BlastTree key={b.check + b.table + b.column} b={b} />)}</div>
        </section>
      )}

      <div className="grid split">
        <section className="card">
          <div className="card-head"><h3>Preflight</h3><span className="muted small">{blocking.length ? `${blocking.length} blocking` : "all clear"}</span></div>
          <div className="card-body">
            {dr.preflight.length === 0 && (
              <p className="small"><span className="mark pass">✓ PASS</span> Required values, types, code lists, lengths, key uniqueness and every
                reference resolved on all {num(dr.totals.source_rows)} rows.</p>
            )}
            <ul className="pf">
              {dr.preflight.map((g) => (
                <li key={g.check + g.table + g.column}>
                  <span className={`mark ${g.blocking ? "fail" : "warn"}`}>{g.blocking ? "✕" : "!"} {g.check}</span>
                  <span><span className="mono">{g.table}.{g.column}</span> — {g.label} <b className="mono">{num(g.rows)}</b> rows
                    <span className="ex mono small muted">
                      {g.examples.slice(0, 3).map((x) => `${x.file}:${x.row}${x.value ? ` “${x.value}”` : ""}`).join(" · ")}
                    </span></span>
                </li>
              ))}
            </ul>
            <div className="table-wrap" style={{ marginTop: 10 }}>
            <table className="map-table">
              <thead><tr><th>Import file</th><th>From</th><th>Rows</th><th>Accepted</th><th>Rejected</th></tr></thead>
              <tbody>
                {dr.tables.map((t) => (
                  <tr key={t.table}><td className="mono">{t.file}</td><td className="mono muted">{t.source_file ?? "—"}</td>
                    <td className="mono">{num(t.source_rows)}</td><td className="mono">{num(t.accepted)}</td>
                    <td className={`mono${t.rejected ? " coral" : ""}`}>{num(t.rejected)}</td></tr>
                ))}
              </tbody>
            </table>
            </div>
          </div>
        </section>
        <section className="card">
          <div className="card-head"><h3>Tie-outs</h3><span className="muted small">legacy → simulated, to the cent</span></div>
          <div className="card-body">
            <ul className="ties">
              {dr.tieouts.map((t) => (
                <li key={t.id}>
                  <span className={`mark ${t.status}`}>{t.status === "pass" ? "✓" : t.status === "warn" ? "!" : "✕"}</span>
                  <span>{t.label}</span>
                  <span className="mono vals">{t.kind === "money" ? money(t.before) : num(t.before)}
                    <span className="eq" aria-hidden="true">{t.before === t.after ? " ═ " : " ≠ "}</span>
                    {t.kind === "money" ? money(t.after) : num(t.after)}</span>
                </li>
              ))}
            </ul>
            <h4 className="sh4" style={{ marginTop: 14 }}>By property · active rent</h4>
            <ul className="ties">
              {dr.properties.map((p) => (
                <li key={p.property}>
                  <span className={`mark ${p.ties ? "pass" : "fail"}`}>{p.ties ? "✓" : "✕"}</span>
                  <span>{p.property}</span>
                  <span className="mono vals">{money(p.legacy.rent_cents)}<span className="eq" aria-hidden="true">{p.ties ? " ═ " : " ≠ "}</span>{p.simulated ? money(p.simulated.rent_cents) : "no match"}</span>
                </li>
              ))}
            </ul>
          </div>
        </section>
      </div>

      <section className="card">
        <div className="card-head"><h3>Predicted findings</h3><span className="muted small">same rules and anchors as the post-import audit</span></div>
        <div className="card-body">
          {dr.predicted.length === 0 ? (
            <p className="small"><span className="mark pass">✓</span> The engine finds nothing wrong with legacy vs simulated: every lease, tenant, deposit and work order
              carries over with the same rent, dates, status and links.</p>
          ) : (
            <ul className="predicted">
              {dr.predicted.slice(0, 14).map((f) => (
                <li key={f.anchor}><SevChip severity={f.severity} /> <span className="mono small">{f.rule_id}</span> <span>{f.title}</span>
                  <span className="muted small"> · {f.subject}</span>
                  {f.rent_risk_cents > 0 && <span className="mono coral small"> −{money(f.rent_risk_cents)}/mo</span>}
                  {f.deposit_cents > 0 && <span className="mono amber small"> {money(f.deposit_cents)} dep.</span>}
                </li>
              ))}
              {dr.predicted.length > 14 && <li className="muted small">…and {num(dr.predicted.length - 14)} more of the same kind.</li>}
            </ul>
          )}
        </div>
      </section>

      <Preview dealId={dealId} dr={dr} />

      <p className="muted small">
        <b>Importer model.</b> {dr.importer_model} Dry run <Code>{dr.id}</Code> · plan v{dr.plan_version}{" "}
        <Code>{dr.plan_hash.slice(0, 12)}</Code> · output <Code>{dr.output_hash.slice(0, 12)}</Code> · {when(dr.created_at)}.{" "}
        {dr.totals.rejected === 0 && pk.critical + pk.high === 0 && <Link to="?step=export">Continue to sign-off →</Link>}
      </p>
    </div>
  );
}
