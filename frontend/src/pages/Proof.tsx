import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Download, Printer } from "lucide-react";
import { downloadUrl, type Comparison, type DryRun, type FindingDetail, type FindingRow, type PlanCounts, type Readiness,
  type ReviewEvent, type Summary } from "../lib/api";
import { ENTITY_LABEL, STATUS_LABEL, day, money, num, pct, when } from "../lib/format";
import { AsciiBar, Empty, ErrorBox, Info, Kicker, Loading, SevChip, StatusPill } from "../components/ui";
import { useRouteDeal } from "./Workspace";

interface Report {
  deal: Record<string, string | null>;
  is_sample: boolean;
  generated_at: string;
  engine: string;
  status: { state: string };
  summary: Summary;
  findings: (FindingRow & { payload: FindingDetail["payload"]; last_review: (ReviewEvent & { finding_id: string }) | null })[];
  events: (ReviewEvent & { finding_id: string; title: string; subject: string; rule_id: string })[];
  exceptions: Report["findings"];
  integration: null | {
    template: { id: string; name: string; version: string };
    plan_hash: string;
    plan_version: number;
    counts: PlanCounts;
    readiness: Readiness;
    dry_run: null | Pick<DryRun, "id" | "created_at" | "plan_hash" | "plan_version" | "output_hash" | "totals" |
      "predicted_kpis" | "banner" | "importer_model">;
    comparison: Comparison;
    receipt: null | { id: string; sha256: string; issued_at: string; verifies: boolean };
  };
}

function IntegrationSection({ ig }: { ig: NonNullable<Report["integration"]> }) {
  const so = ig.readiness.signoff;
  const c = ig.comparison;
  return (
    <>
      <h2 className="sh"><span className="n">§12</span> Integration plan, dry run &amp; release gates</h2>
      <p style={{ fontSize: 14 }}>
        Template <b>{ig.template.name}</b> ({ig.template.version}) · plan <span className="mono">{ig.plan_hash.slice(0, 12)}</span> v{ig.plan_version} ·{" "}
        {ig.counts.decided}/{ig.counts.total} columns decided by a named reviewer ·{" "}
        {so && !so.revoked_at ? <>signed off by <b>{so.reviewer}</b> (single reviewer) {when(so.created_at)}</> : "not signed off"}.
      </p>
      {ig.dry_run && (
        <p className="muted" style={{ fontSize: 13 }}>
          <b>{ig.dry_run.banner}.</b> Dry run {ig.dry_run.id}: {num(ig.dry_run.totals.accepted)}/{num(ig.dry_run.totals.source_rows)} rows
          simulated, {num(ig.dry_run.totals.rejected)} rejected, {ig.dry_run.predicted_kpis.findings} predicted finding(s).
        </p>
      )}
      {c.available && c.tieouts && (
        <table className="ledger">
          <tbody>
            <tr><td className="muted">Check</td><td className="amt muted">Legacy</td><td className="amt muted">Simulated</td><td className="amt muted">Actual</td></tr>
            {c.tieouts.map((t) => {
              const f = (v: number | null) => (v === null ? "—" : t.kind === "money" ? money(v) : num(v));
              return (
                <tr key={t.id}>
                  <td>{t.label}</td><td className="amt">{f(t.legacy)}</td><td className="amt">{f(t.simulated)}</td>
                  <td className="amt" style={t.actual_ties ? undefined : { color: "var(--coral-ink)" }}>{f(t.actual)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {c.available && c.counts && (
        <p style={{ fontSize: 13.5, marginTop: 8 }}>
          {c.counts.predicted} predicted · {c.counts.observed} observed after the load · <b>{c.counts.observed_only}</b> import-time defect(s)
          the dry run could not have seen. {c.note}
        </p>
      )}
      <ul className="checklist" style={{ marginTop: 10 }}>
        {ig.readiness.gates.map((g) => (
          <li key={g.id}>
            <span className={`mark ${g.status === "pending" ? "" : g.status}`}>{g.id} {g.status === "pass" ? "✓" : g.status === "fail" ? "✕" : "·"}</span>
            <span>{g.label}</span><span className="vals">{g.status}</span><span className="note">{g.detail}</span>
          </li>
        ))}
      </ul>
      {ig.receipt && (
        <p className="mono" style={{ fontSize: 12, marginTop: 8 }}>
          Release receipt {ig.receipt.id} · {when(ig.receipt.issued_at)} · sha256 {ig.receipt.sha256}
          {!ig.receipt.verifies && " — stored receipt no longer matches its hash"}
        </p>
      )}
    </>
  );
}

const ACTION: Record<string, string> = {
  investigate: "Investigating", expected_exception: "Expected exception", resolve: "Resolved", reopen: "Reopened",
  comment: "Comment", confirm_match: "Confirmed match", evidence_changed: "Evidence changed",
};

function useReport(id: string) {
  return useQuery({ queryKey: ["report", id], queryFn: () => fetchReport(id) });
}

async function fetchReport(id: string): Promise<Report> {
  const res = await fetch(downloadUrl(`/deals/${id}/report`));
  if (!res.ok) throw new Error("Couldn't load the report.");
  return res.json();
}

function Path({ f }: { f: Report["findings"][number] }) {
  const parts = f.subject.split(" · ");
  const tenant = parts[0];
  const unit = parts[1];
  const prop = parts.slice(2).join(" · ");
  const lease = f.source_ref?.id;
  const nodes = [prop, unit, lease ? `lease ${lease}` : null, tenant].filter(Boolean);
  return <span className="path">{nodes.join(" ── ")} ──▶</span>;
}

export function Proof() {
  const id = useRouteDeal();
  const rq = useReport(id);
  if (rq.isLoading) return <div className="page"><Loading label="Assembling the continuity proof" /></div>;
  if (rq.error) return <div className="page"><ErrorBox error={rq.error} onRetry={() => rq.refetch()} /></div>;
  const r = rq.data!;
  const s = r.summary;
  if (!s.has_run) {
    return (
      <div className="page">
        <Kicker sec="05">Continuity proof</Kicker>
        <Empty title="Nothing to prove yet" actions={<Link className="btn primary" to={`/deals/${id}/workspace`}>Run the audit</Link>}>
          The report is generated from a completed audit run and the current review decisions.
        </Empty>
      </div>
    );
  }
  const k = s.kpis!;
  const c = s.continuity!;
  const d = r.deal;
  const sev = s.severity_open!;
  const maxSev = Math.max(1, ...Object.values(sev));
  const byId = new Map(r.findings.map((f) => [f.id, f]));
  const deposits = r.findings.filter((f) => f.deposit_cents > 0);
  const outstanding = r.findings.filter((f) => (f.status === "open" || f.status === "investigating") && f.confidence === "needs_review");

  return (
    <div className="page">
      <div className="page-head no-print">
        <div>
          <Kicker sec="05">Continuity proof</Kicker>
          <h1 className="page-title">The report for the <em>integration meeting</em></h1>
          <p className="page-sub">Generated from the latest run plus every review decision so far. Exports include current status.</p>
        </div>
        <div className="btn-row">
          <a className="btn primary" href={downloadUrl(`/deals/${id}/report.pdf`)} download><Download /> Download PDF</a>
          <a className="btn ghost" href={downloadUrl(`/deals/${id}/findings.csv`)} download><Download /> Findings CSV</a>
          <button className="btn ghost" onClick={() => window.print()}><Printer /> Print</button>
        </div>
      </div>
      {s.run?.stale && (
        <div className="banner no-print">
          <span className="ico">↻</span>
          <span className="grow">Data or match decisions changed after this run — rerun the audit before sharing.</span>
          <Link className="btn sm ink" to={`/deals/${id}/workspace`}>Rerun</Link>
        </div>
      )}

      <article className="sheet">
        <header className="sheet-head">
          <div>
            <Kicker plain>Migration continuity report{r.is_sample ? " · synthetic demonstration data" : ""}</Kicker>
            <h1>{d.title}</h1>
          </div>
          <div className="seal" aria-label={`Continuity ${pct(c.score)}`}>
            <span>
              MergeProof
              <b>{pct(c.score)}</b>
              continuity
            </span>
          </div>
        </header>
        <dl className="meta-grid">
          {[
            ["Acquirer", d.acquirer], ["Business integrated", d.target_business], ["Legacy system", d.old_system],
            ["Consolidated system", d.new_system], ["Cutover / as-of", day(s.as_of)], ["Audit run", when(s.run?.finished_at)],
            ["Engine", r.engine], ["Report generated", when(r.generated_at)],
          ].map(([a, b]) => (
            <div key={a as string}><dt>{a}</dt><dd>{b || "—"}</dd></div>
          ))}
        </dl>

        <h2 className="sh"><span className="n">§01</span> Summary</h2>
        <table className="ledger">
          <tbody>
            <tr>
              <td>Continuity score — active legacy leases accounted for
                <span className="path">{c.accounted} of {c.active_leases} ({c.matched} matched automatically, {c.accounted - c.matched} closed by review) · {c.clean} clean</span></td>
              <td className="amt">{pct(c.score)}</td>
            </tr>
            <tr>
              <td>Records verified one-to-one (properties, units, tenants, leases, work orders)</td>
              <td className="amt">{num(k.records_verified)} / {num(k.records_total)}</td>
            </tr>
            <tr>
              <td>Open findings <span className="path">{k.critical_open} critical · {k.high_open} high · {k.findings_closed} closed by review</span></td>
              <td className="amt">{k.findings_open}</td>
            </tr>
            <tr>
              <td>Estimated monthly rent at risk (requires review)</td>
              <td className="amt" style={{ color: "var(--coral-ink)" }}>{money(k.rent_at_risk_open, { decimals: true })}</td>
            </tr>
            <tr>
              <td>Deposit balance discrepancies — liability, tracked separately</td>
              <td className="amt" style={{ color: "var(--amber-ink)" }}>{money(k.deposit_open, { decimals: true })}</td>
            </tr>
          </tbody>
        </table>

        <h2 className="sh"><span className="n">§02</span> Estimated monthly rent at risk
          <Info>Requires review. Not realized revenue loss.</Info>
        </h2>
        <p className="muted" style={{ fontSize: 14, marginBottom: 10 }}>
          Method: each legacy lease contributes <b>at most one</b> amount — the largest open exposure among its findings.
          A missing or inactivated lease contributes its full monthly rent; a lower rent on a matched lease contributes the
          difference. Rent increases, leases still under review and deposit differences are excluded.
        </p>
        <table className="ledger">
          <tbody>
            {s.rent_lines!.length === 0 && <tr><td className="muted">No open rent exposure.</td><td className="amt">{money(0)}</td></tr>}
            {s.rent_lines!.map((l) => {
              const f = byId.get(l.finding_id);
              return (
                <tr key={l.risk_key}>
                  <td>
                    <Link to={`/deals/${id}/findings?f=${l.finding_id}`}>{l.rule_id} · {l.title}</Link> — {l.subject}
                    {f && <Path f={f} />}
                    {l.also.map((a) => (
                      <span className="also" key={a.finding_id}>
                        also {a.rule_id} {a.title} ({money(a.cents, { decimals: true })}) — same lease, not added
                      </span>
                    ))}
                  </td>
                  <td className="amt">{money(l.cents, { decimals: true })}</td>
                </tr>
              );
            })}
            <tr className="total">
              <td>Total estimated monthly rent at risk</td>
              <td className="amt">{money(k.rent_at_risk_open, { decimals: true })}</td>
            </tr>
          </tbody>
        </table>
        <p className="muted" style={{ fontSize: 13, marginTop: 8 }}>
          Under review, not counted: {money(k.pending_review_rent, { decimals: true })}/mo · detected before review decisions:{" "}
          {money(k.rent_at_risk_detected, { decimals: true })}/mo
        </p>

        <h2 className="sh"><span className="n">§03</span> Deposit balance discrepancies</h2>
        <table className="ledger">
          <tbody>
            {deposits.length === 0 && <tr><td className="muted">No deposit discrepancies.</td><td className="amt">—</td></tr>}
            {deposits.map((f) => (
              <tr key={f.id}>
                <td>
                  <Link to={`/deals/${id}/findings?f=${f.id}`}>{f.rule_id} · {f.title}</Link> — {f.subject}{" "}
                  <span className="path">{STATUS_LABEL[f.status]} · {f.source_ref?.file}:r{f.source_ref?.row}</span>
                </td>
                <td className="amt">{money(f.deposit_cents, { decimals: true })}</td>
              </tr>
            ))}
            <tr className="total"><td>Open deposit discrepancies (not added to rent)</td><td className="amt">{money(k.deposit_open, { decimals: true })}</td></tr>
          </tbody>
        </table>

        <h2 className="sh"><span className="n">§04</span> Portfolio totals</h2>
        <p className="muted" style={{ fontSize: 14, marginBottom: 6 }}>
          Equal totals can hide offsetting errors, so every total is paired with the row-level evidence above.
        </p>
        <ul className="checklist">
          {s.checks!.map((ch) => {
            const fmt = (v: number) => (ch.kind === "money" ? money(v) : num(v));
            return (
              <li key={ch.id}>
                <span className={`mark ${ch.status}`}>[{ch.status === "pass" ? "PASS" : ch.status === "warn" ? "WARN" : "FAIL"}]</span>
                <span>{ch.label}</span>
                <span className="vals">{fmt(ch.before)} → {fmt(ch.after)}</span>
                <span className="note">{ch.note}</span>
              </li>
            );
          })}
        </ul>

        <h2 className="sh"><span className="n">§05</span> Record coverage</h2>
        <div className="bars">
          {Object.entries(s.counts!).filter(([e]) => e !== "deposits").map(([e, ct]) => {
            const m = (ct.confirmed ?? 0) + (ct.probable ?? 0) + (ct.reviewer_confirmed ?? 0);
            return (
              <div className="b" key={e}>
                <span className="lbl">{ENTITY_LABEL[e] ?? e}</span>
                <span className="fill" style={{ color: "var(--emerald)" }}><AsciiBar fraction={ct.before ? m / ct.before : 0} width={30} /></span>
                <span className="val">{num(m)}/{num(ct.before)}</span>
              </div>
            );
          })}
        </div>
        <p className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>
          Matched = confirmed, probable or reviewer-confirmed one-to-one pairs. Deposits are reconciled through their
          matched lease.
        </p>
        {s.benign && (
          <>
            <h3 className="serif" style={{ fontSize: 22, margin: "18px 0 6px" }}>Recognized as harmless — no finding raised</h3>
            <table className="ledger">
              <tbody>
                {Object.entries(s.benign).map(([kk, v]) => (
                  <tr key={kk}>
                    <td>{kk}<span className="path">e.g. {v.examples.slice(0, 2).map(([a, b]) => `${a} → ${b}`).join(" · ")}</span></td>
                    <td className="amt">{num(v.count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}

        <h2 className="sh"><span className="n">§06</span> Checks performed</h2>
        <ul className="checklist">
          {s.rules!.map((ru) => (
            <li key={ru.rule_id}>
              <span className={`mark ${ru.findings ? "fail" : "pass"}`}>{ru.rule_id}</span>
              <span>{ru.name}</span>
              <span className="vals">{num(ru.records_checked)} checked · {ru.findings}</span>
              <span className="note">{ru.check}</span>
            </li>
          ))}
        </ul>

        <h2 className="sh"><span className="n">§07</span> Open findings by severity</h2>
        <div className="bars">
          {(["critical", "high", "medium", "low"] as const).map((sv) => (
            <div className="b" key={sv}>
              <span className="lbl"><SevChip severity={sv} /></span>
              <span className="fill"><AsciiBar fraction={sev[sv] / maxSev} width={30} /></span>
              <span className="val">{sev[sv]}</span>
            </div>
          ))}
        </div>

        <h2 className="sh"><span className="n">§08</span> Outstanding decisions</h2>
        {outstanding.length === 0 ? <p className="muted">No ambiguous matches are awaiting a decision.</p> : (
          <ul className="checklist">
            {outstanding.map((f) => (
              <li key={f.id}>
                <span className="mark warn">[DECIDE]</span>
                <span><Link to={`/deals/${id}/findings?f=${f.id}`}>{f.title}</Link></span>
                <span className="vals">{f.pending_rent_cents ? `${money(f.pending_rent_cents)}/mo` : ""}</span>
                <span className="note">{f.subject} — {f.payload.recommended_action}</span>
              </li>
            ))}
          </ul>
        )}

        <h2 className="sh"><span className="n">§09</span> Expected exceptions &amp; notes</h2>
        {r.exceptions.length === 0 ? <p className="muted">No findings have been marked as expected exceptions.</p> : (
          <table className="ledger">
            <tbody>
              {r.exceptions.map((f) => (
                <tr key={f.id}>
                  <td>
                    <b>{f.title}</b> — {f.subject}
                    <span className="path">“{f.last_review?.note}” — {f.last_review?.reviewer}, {when(f.last_review?.created_at)}</span>
                  </td>
                  <td className="amt">{f.rent_risk_cents ? money(f.rent_risk_cents) : f.deposit_cents ? money(f.deposit_cents) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <h2 className="sh"><span className="n">§10</span> Review log</h2>
        {r.events.length === 0 ? <p className="muted">No review actions yet.</p> : (
          <div className="table-wrap">
            <table className="t compact">
              <thead><tr><th>When</th><th>Reviewer</th><th>Action</th><th>Finding</th><th>Note</th></tr></thead>
              <tbody>
                {[...r.events].reverse().map((e) => (
                  <tr key={e.id}>
                    <td className="ref">{when(e.created_at)}</td>
                    <td>{e.reviewer}</td>
                    <td>{ACTION[e.action] ?? e.action} <span className="muted mono" style={{ fontSize: 11 }}>{e.from_status}→{e.to_status}</span></td>
                    <td><span className="mono" style={{ fontSize: 12 }}>{e.finding_id}</span> {e.title}</td>
                    <td style={{ maxWidth: 280 }}>{e.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <h2 className="sh"><span className="n">§11</span> Evidence appendix</h2>
        <div className="table-wrap">
          <table className="t compact">
            <thead><tr><th>Finding</th><th>Subject</th><th>Legacy row</th><th>Consolidated row</th><th>Status</th></tr></thead>
            <tbody>
              {r.findings.map((f) => (
                <tr key={f.id}>
                  <td><SevChip severity={f.severity} /> <Link to={`/deals/${id}/findings?f=${f.id}`}>{f.title}</Link><span className="rid">{f.rule_id} · {f.id}</span></td>
                  <td>{f.subject}</td>
                  <td className="ref">{f.source_ref ? `${f.source_ref.file}:r${f.source_ref.row}` : "—"}</td>
                  <td className="ref">{f.target_ref ? `${f.target_ref.file}:r${f.target_ref.row}` : "—"}</td>
                  <td><StatusPill status={f.status} rereview={f.needs_rereview} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {r.integration && <IntegrationSection ig={r.integration} />}

        <h2 className="sh"><span className="n">§{r.integration ? "13" : "12"}</span> AI assistance</h2>
        {s.ai?.available ? (
          <p style={{ fontSize: 14 }}>
            A local open-source model ({s.ai.model}) ran on the MergeProof server. It made{" "}
            <b>{s.ai.suggestions}</b> suggestion{s.ai.suggestions === 1 ? "" : "s"} for a reviewer ({s.ai.used_for.join("; ")})
            and <b>{s.ai.decisions_by_ai}</b> decisions. Every finding, match, amount and score in this report comes from
            the deterministic rules or a named reviewer's decision. No data was sent to an external service.
          </p>
        ) : (
          <p style={{ fontSize: 14 }}>
            AI assistance was not used for this run ({s.ai?.reason ?? "not available"}). Every result comes from the
            deterministic rules.
          </p>
        )}

        <hr className="dash" />
        <p className="muted" style={{ fontSize: 12.5 }}>
          <b>Limitations.</b> MergeProof compares customer-provided exports; it does not connect to or modify any
          property-management system. Matching is deterministic and rule-based; ambiguous pairings are left for a reviewer.
          Amounts are estimates for review — not accounting or legal conclusions.
          {r.is_sample && " All names, addresses and amounts in this report are synthetic demonstration data."}
        </p>
      </article>
    </div>
  );
}
