import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, FileCheck2, Sparkles, Upload } from "lucide-react";
import { api, type Summary } from "../lib/api";
import { useAppState, usePrefersReducedMotion } from "../lib/store";
import { money, num, pct, plural } from "../lib/format";
import { AsciiBar, DealStateChip, ErrorBox, Info, Kicker, Loading, SevChip, useCountUp } from "../components/ui";
import { IllustrativeSkyline, Skyline } from "../components/Skyline";
import { RunAudit } from "../components/RunAudit";
import { DealForm } from "../components/DealForm";
import { useSampleLoader } from "../components/Shell";

function ScoreGauge({ s }: { s: Summary }) {
  const c = s.continuity!;
  const reduced = usePrefersReducedMotion();
  const shown = useCountUp(Math.round((c.score ?? 0) * 10), 900, !reduced) / 10;
  return (
    <div className="score">
      <div className="label">
        Continuity score
        <Info>
          Share of the <b>{c.active_leases}</b> leases that were active in {s.labels?.old} which have a confident
          counterpart in {s.labels?.new} — or whose finding a reviewer has closed. {c.matched} matched automatically;
          {" "}{c.accounted - c.matched} closed by review. It measures reconciliation completeness, not legal compliance
          or the overall quality of the merger.
        </Info>
      </div>
      <div className="value">
        <span className="big">{shown.toFixed(1)}%</span>
        <AsciiBar fraction={(c.score ?? 0) / 100} width={26} />
      </div>
      <div className="den">
        {c.accounted} of {c.active_leases} active leases accounted for · {c.clean} clean ({pct(c.clean_pct)}) with no open
        discrepancy
      </div>
    </div>
  );
}

function Kpis({ s }: { s: Summary }) {
  const k = s.kpis!;
  const reduced = usePrefersReducedMotion();
  const rent = useCountUp(k.rent_at_risk_open, 900, !reduced);
  const dep = useCountUp(k.deposit_open, 900, !reduced);
  return (
    <section className="kpis" aria-label="Key results">
      <div className="kpi tone-lime">
        <div className="tape" />
        <div className="k"><span className="n">01</span> Verified records</div>
        <div className="v">
          {num(k.records_verified)}<small>/ {num(k.records_total)}</small>
        </div>
        <div className="s">
          <b>{k.records_needs_review}</b> need review · paired one-to-one across properties, units, tenants, leases and
          work orders
        </div>
      </div>
      <div className="kpi tone-coral">
        <div className="tape" />
        <div className="k"><span className="n">02</span> Critical findings</div>
        <div className="v">{k.critical_open}</div>
        <div className="s">
          <b>{k.high_open}</b> high · <b>{k.findings_open}</b> open of {k.findings_total} · {k.findings_closed} closed
        </div>
      </div>
      <div className="kpi tone-coral">
        <div className="tape" />
        <div className="k">
          <span className="n">03</span> Est. monthly rent at risk
          <Info>
            Requires review — not “revenue lost”. Each legacy lease contributes at most once: a missing or inactivated
            lease adds its full monthly rent; a lower rent on a matched lease adds the difference. Rent increases,
            leases still under review and deposit differences are excluded. Only open findings count.
          </Info>
        </div>
        <div className="v">
          {money(rent)}<small>/mo</small>
        </div>
        <div className="s">
          requires review · {k.pending_review_rent > 0 ? <><b>{money(k.pending_review_rent)}</b> more under review, not counted</> : "nothing pending review"}
        </div>
      </div>
      <div className="kpi tone-amber">
        <div className="tape" />
        <div className="k">
          <span className="n">04</span> Deposit discrepancies
          <Info>
            Security deposits are a liability owed to tenants, so they are reconciled separately and never added to rent
            at risk.
          </Info>
        </div>
        <div className="v">{money(dep)}</div>
        <div className="s">
          <b>{k.deposit_open_count}</b> open · tenant liability, tracked separately from rent
        </div>
      </div>
    </section>
  );
}

function BlindSpot({ s }: { s: Summary }) {
  const agg = Object.fromEntries((s.checks ?? []).map((c) => [c.id, c]));
  const leases = agg["AGG-1"];
  const rent = agg["AGG-2"];
  const k = s.kpis!;
  if (!leases || !rent) return null;
  const atRisk = s.rent_lines?.length ?? 0;
  const equal = leases.before === leases.after;
  return (
    <section className="callout">
      <div className="eq tnum">
        {leases.before} {equal ? "=" : <span className="ne">≠</span>} {leases.after}
      </div>
      <p>
        <b>Active lease counts {equal ? "match" : "differ"}</b> ({leases.before} before, {leases.after} after) and
        portfolio rent differs by just <b>{money(rent.delta, { signed: true })}/mo</b> — yet row-level checks show{" "}
        <b>{plural(atRisk, "lease")}</b> with <b>{money(k.rent_at_risk_open)}/mo</b> at risk
        {k.records_needs_review ? <>, and <b>{k.records_needs_review}</b> match waiting on a human</> : null}. Moving data
        isn’t proving it survived: totals net gains against losses.
      </p>
    </section>
  );
}

function Attention({ s, dealId }: { s: Summary; dealId: string }) {
  const nav = useNavigate();
  const items = s.attention ?? [];
  return (
    <section className="card ticks">
      <div className="card-head">
        <h3>What needs attention now?</h3>
        <Link to={`/deals/${dealId}/findings`} className="btn sm ghost">
          View all findings <ArrowRight />
        </Link>
      </div>
      {items.length === 0 ? (
        <div className="card-body muted">Nothing open. Every finding has been reviewed.</div>
      ) : (
        <div className="attn">
          {items.map((f, i) => {
            const amt = f.rent_counted_cents || f.rent_risk_cents;
            return (
              <button key={f.id} className="attn-item" onClick={() => nav(`/deals/${dealId}/findings?f=${f.id}`)}>
                <span className="no" aria-hidden="true">{String(i + 1).padStart(2, "0")}</span>
                <span style={{ minWidth: 0 }}>
                  <span className="title">
                    <SevChip severity={f.severity} /> {f.title}
                  </span>
                  <span className="subj" style={{ display: "block" }}>{f.subject}</span>
                  <span className="why">{f.explanation}</span>
                </span>
                <span className="amt">
                  {amt ? (
                    <>−{money(amt)}<small>PER MONTH</small></>
                  ) : f.deposit_cents ? (
                    <>{money(f.deposit_cents)}<small>DEPOSIT</small></>
                  ) : f.pending_rent_cents ? (
                    <>{money(f.pending_rent_cents)}<small>UNDER REVIEW</small></>
                  ) : (
                    <span className="mono muted" style={{ fontSize: 12 }}>{f.rule_id}</span>
                  )}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

function Flow({ s }: { s: Summary }) {
  const files = s.files ?? [];
  const before = files.filter((f) => f.side === "before").reduce((a, f) => a + f.rows, 0);
  const after = files.filter((f) => f.side === "after").reduce((a, f) => a + f.rows, 0);
  const k = s.kpis!;
  const nRules = s.rules?.length ?? 0;
  const nChecks = s.checks?.length ?? 0;
  return (
    <section className="card pad">
      <Kicker sec="↯">How this result was produced</Kicker>
      <div className="flow" aria-label="Audit pipeline">
        <div className="node">
          <div className="t">Legacy export</div>
          <div className="n">{num(before)}</div>
          <div className="d">rows · {files.filter((f) => f.side === "before").length} files</div>
        </div>
        <div className="arrow" aria-hidden="true">──▶</div>
        <div className="node">
          <div className="t">Entity matching</div>
          <div className="n">{num(k.records_verified)}</div>
          <div className="d">pairs vs {num(after)} consolidated rows</div>
        </div>
        <div className="arrow" aria-hidden="true">──▶</div>
        <div className="node">
          <div className="t">Checks</div>
          <div className="n">{nRules + nChecks}</div>
          <div className="d">{nRules} record-level · {nChecks} totals · {k.findings_total} findings</div>
        </div>
        <div className="arrow" aria-hidden="true">──▶</div>
        <div className="node end">
          <div className="t">Verified target</div>
          <div className="n">{pct(s.continuity?.score)}</div>
          <div className="d">active leases accounted for</div>
        </div>
      </div>
      {s.ai && (
        <p className="muted" style={{ fontSize: 13, marginTop: 12, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <span className="ai-badge">AI</span>
          {s.ai.available
            ? <>Local model {s.ai.model} made {s.ai.suggestions} suggestion{s.ai.suggestions === 1 ? "" : "s"} for review and {s.ai.decisions_by_ai} decisions. Every number above comes from the rules.</>
            : <>AI assist is off ({s.ai.reason}). Everything above comes from the rules.</>}
          <Link to="/about#ai" className="link-btn">How AI is used</Link>
        </p>
      )}
    </section>
  );
}

function Harmless({ s }: { s: Summary }) {
  const rows = Object.entries(s.benign ?? {}).slice(0, 6);
  if (!rows.length) return null;
  return (
    <section className="card pad">
      <Kicker sec="✓">Recognized as harmless</Kicker>
      <p className="muted" style={{ fontSize: 13, marginBottom: 10 }}>
        Differences normalization proved equal — no finding raised, no false alarm.
      </p>
      <table className="ledger">
        <tbody>
          {rows.map(([label, v]) => (
            <tr key={label}>
              <td>
                {label}
                {v.examples[0] && <span className="path">{v.examples[0][0]} → {v.examples[0][1]}</span>}
              </td>
              <td className="amt">{num(v.count)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Severity({ s }: { s: Summary }) {
  const sev = s.severity_open!;
  const max = Math.max(1, ...Object.values(sev));
  return (
    <section className="card pad">
      <Kicker sec="≡">Open by severity</Kicker>
      <div className="bars">
        {(["critical", "high", "medium", "low"] as const).map((k) => (
          <div className="b" key={k}>
            <span className="lbl"><SevChip severity={k} /></span>
            <span className="fill" style={{ color: k === "critical" ? "var(--coral-ink)" : k === "high" ? "#c2622d" : k === "medium" ? "var(--amber-ink)" : "var(--muted)" }}>
              <AsciiBar fraction={sev[k] / max} width={22} />
            </span>
            <span className="val">{sev[k]}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

export function CommandCenter() {
  const { dealId, dealsLoading } = useAppState();
  const nav = useNavigate();
  const sample = useSampleLoader();
  const [creating, setCreating] = useState(false);
  const dealQ = useQuery({ queryKey: ["deal", dealId], queryFn: () => api.deal(dealId!), enabled: !!dealId });
  const sumQ = useQuery({ queryKey: ["summary", dealId], queryFn: () => api.summary(dealId!), enabled: !!dealId });
  const hasRun = !!sumQ.data?.has_run;
  const mapQ = useQuery({ queryKey: ["unitmap", dealId], queryFn: () => api.unitMap(dealId!), enabled: !!dealId && hasRun });
  const deal = dealQ.data;
  const s = sumQ.data;

  if (dealsLoading || (dealId && (dealQ.isLoading || sumQ.isLoading))) return <div className="page"><Loading /></div>;

  const noDeal = !dealId || !deal;
  return (
    <div className="page">
      {(dealQ.error || sumQ.error) && <ErrorBox error={dealQ.error || sumQ.error} onRetry={() => { dealQ.refetch(); sumQ.refetch(); }} />}
      <section className="hero on-ink">
        <div className="hero-inner">
          <div>
            {deal && (
              <div className="hero-deal">
                <span className="dot" aria-hidden="true" /> {deal.title}
                {deal.is_sample && <span style={{ color: "var(--ink-400)" }}>· synthetic</span>}
              </div>
            )}
            <Kicker sec="01">Command Center</Kicker>
            <h1>
              Did every lease <em>survive</em> the move?
            </h1>
            <p className="lede">
              {hasRun && s?.labels
                ? `${s.labels.old} vs ${s.labels.new}: ${num(s.totals?.active_leases)} active leases (${money(s.totals?.active_rent)}/mo) checked record by record. Here is what the evidence shows.`
                : "After an acquisition, MergeProof proves which tenants, leases, deposits and work orders survived the move into your main system — and shows exactly what went missing or changed, down to the source row."}
            </p>
            <div className="btn-row">
              {noDeal || !hasRun ? (
                <>
                  {noDeal || !deal?.is_sample ? (
                    <button className="btn primary lg" onClick={() => sample.mutate(false)} disabled={sample.isPending}>
                      <Sparkles /> {sample.isPending ? "Loading sample…" : "Try sample acquisition"}
                    </button>
                  ) : (
                    <Link className="btn primary lg" to={`/deals/${deal.id}/workspace`}>
                      <FileCheck2 /> Review exports &amp; run audit
                    </Link>
                  )}
                  <button className="btn ghost lg" onClick={() => setCreating(true)}>
                    <Upload /> Start audit with my files
                  </button>
                </>
              ) : (
                <>
                  <Link className="btn primary lg" to={`/deals/${deal!.id}/findings`}>
                    Review findings <ArrowRight />
                  </Link>
                  <Link className="btn ghost lg" to={`/deals/${deal!.id}/proof`}>
                    <FileCheck2 /> Continuity proof
                  </Link>
                </>
              )}
            </div>
            {hasRun && s ? <ScoreGauge s={s} /> : (
              <p className="fine">No account, card or real data needed · fictional Cedar × Bayview portfolio · 186 units</p>
            )}
          </div>
          <div>
            {hasRun && mapQ.data ? (
              <>
                <Skyline properties={mapQ.data.properties} lit onPick={(id) => nav(`/deals/${dealId}/findings?f=${id}`)} />
                <div className="skyline-caption">
                  Each window is a unit in {s?.labels?.old_short}'s legacy export · click a flagged window for evidence
                </div>
              </>
            ) : (
              <>
                <IllustrativeSkyline />
                <div className="skyline-caption">
                  Illustrative · after an audit every window is a real unit, coloured by what the evidence shows
                </div>
              </>
            )}
          </div>
        </div>
      </section>

      {deal && !hasRun && (
        <div className="section grid cols-2" style={{ alignItems: "start" }}>
          <div className="card pad">
            <Kicker sec="02">Ready when you are</Kicker>
            <h3 className="serif" style={{ fontSize: 30, lineHeight: 1.05 }}>{deal.title}</h3>
            <div className="row" style={{ marginTop: 10 }}>
              <DealStateChip state={deal.status.state} />
              <span className="muted" style={{ fontSize: 13 }}>
                {deal.snapshots?.filter((x) => x.side === "before").length ?? 0} legacy files ·{" "}
                {deal.snapshots?.filter((x) => x.side === "after").length ?? 0} consolidated files
              </span>
            </div>
            <p className="muted" style={{ marginTop: 12, fontSize: 14 }}>
              The audit pairs every legacy record with its consolidated counterpart, tests tenant → lease → unit →
              property relationships, and keeps the source row behind every finding.
            </p>
            <div className="btn-row" style={{ marginTop: 16 }}>
              <Link to={`/deals/${deal.id}/workspace`} className="btn ghost">Review exports &amp; mapping</Link>
            </div>
          </div>
          <RunAudit deal={deal} />
        </div>
      )}

      {hasRun && s && deal && (
        <>
          {s.run?.stale && (
            <div className="banner section">
              <span className="ico">↻</span>
              <span className="grow">
                {s.run.match_decisions_pending
                  ? `${plural(s.run.match_decisions_pending, "match decision")} not yet applied to downstream checks.`
                  : "Exports or mappings changed since the last run."}{" "}
                Rerun to refresh the evidence — reviewed decisions are kept.
              </span>
              <Link to={`/deals/${deal.id}/workspace`} className="btn sm ink">Rerun audit</Link>
            </div>
          )}
          <div className="section"><Kpis s={s} /></div>
          <div className="section"><BlindSpot s={s} /></div>
          <div className="section grid split">
            <Attention s={s} dealId={deal.id} />
            <div className="stack">
              <Severity s={s} />
              <Harmless s={s} />
            </div>
          </div>
          <div className="section"><Flow s={s} /></div>
        </>
      )}

      {noDeal && (
        <section className="section grid cols-3">
          {[
            ["01", "Bring two exports", "Legacy (before) and consolidated (after) CSV or XLSX files: properties, units, tenants, leases, deposits, work orders."],
            ["02", "Prove every record", "Deterministic matching that survives new IDs and reformatting, then business checks: missing leases, rent changes, deposits, duplicates, broken links, lost work orders."],
            ["03", "Decide and export", "Inspect side-by-side evidence, confirm or except each finding, and export a Continuity Report your owner or accountant can read."],
          ].map(([n, t, d]) => (
            <div key={n} className="card pad ticks">
              <div className="mono muted" style={{ fontSize: 12 }}>§{n}</div>
              <h3 className="serif" style={{ fontSize: 28, marginTop: 6 }}>{t}</h3>
              <p className="muted" style={{ marginTop: 8, fontSize: 14 }}>{d}</p>
            </div>
          ))}
        </section>
      )}
      {creating && <DealForm onClose={() => setCreating(false)} />}
    </div>
  );
}
