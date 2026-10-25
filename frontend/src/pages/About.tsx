import { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Kicker } from "../components/ui";

function pct(x: number | null | undefined, d = 1) {
  return x === null || x === undefined ? "—" : `${(x * 100).toFixed(d)}%`;
}

function AiSection() {
  const q = useQuery({ queryKey: ["ai"], queryFn: api.ai, staleTime: 60_000 });
  const loc = useLocation();
  useEffect(() => {
    if (loc.hash === "#ai" && q.data) document.getElementById("ai")?.scrollIntoView({ block: "start" });
  }, [loc.hash, q.data]);
  const card = q.data;
  const ev = card?.evaluation;
  const ro = ev?.rules_only;
  const wa = ev?.with_ai;
  const h0 = ev?.headers.rules_only.summary;
  const h1 = ev?.headers.with_ai?.summary;
  return (
    <section id="ai" style={{ scrollMarginTop: 80 }}>
      <h3>AI, used carefully</h3>
      <p>
        MergeProof's findings come from deterministic rules you can reproduce. A small open-source language model runs
        on the same server (no API, no cost, no data leaving the machine) and helps in two narrow places, always as a
        labelled suggestion that a person has to accept:
      </p>
      <ul>
        {(card?.does ?? []).map((d) => <li key={d}>{d}</li>)}
      </ul>
      <p><b>It never:</b></p>
      <ul>
        {(card?.never ?? []).map((d) => <li key={d}>{d}</li>)}
      </ul>
      <p className="muted" style={{ fontSize: 14 }}>
        Status: {card ? (card.status.available ? `on · ${card.status.model} (local)` : card.status.enabled ? `on, model not loaded (${card.status.reason})` : "off") : "…"}
        {" "}· turn it off with <span className="mono">{card?.off_switch ?? "MERGEPROOF_AI=off"}</span>, and the engine runs rules only.
      </p>
      {ro && (
        <>
          <p>
            <b>How we know it works.</b> We generate {ro.seeds} randomized migrations from the legacy sample. Each one
            re-keys every ID, reshuffles rows, renames headers, reformats every value, blanks some tenant contact
            details, and injects known anomalies. Because we made every change, we know the right answer and can score
            the engine against it.
          </p>
          <div className="scorecard" aria-label="Evaluation scorecard">
            <div><div className="k">Anomalies caught</div><div className="v">{pct(ro.anomalies.recall)}</div>
              <div className="s">{ro.anomalies.detected} of {ro.anomalies.injected}; every other one was sent to review</div></div>
            <div><div className="k">Wrong claims</div><div className="v">{ro.anomalies.false_alarms}</div>
              <div className="s">{ro.anomalies.routed_to_review} uncertain cases routed to a person instead</div></div>
            <div><div className="k">Matching precision</div><div className="v">{pct(Math.min(...Object.values(ro.matching).map((m) => m.precision)))}</div>
              <div className="s">worst record type; never paired the wrong records</div></div>
            <div><div className="k">Money exact</div><div className="v">{ro.money.rent_at_risk_exact}/{ro.money.of}</div>
              <div className="s">rent at risk and deposits to the cent on every run</div></div>
            {wa && (
              <div><div className="k">AI: rewritten work orders</div><div className="v">{wa.ai_work_orders.top1}/{wa.ai_work_orders.rewrites}</div>
                <div className="s">top suggestion right; the rules alone call these missing</div></div>
            )}
            {wa && (
              <div><div className="k">AI: false suggestions</div><div className="v">{wa.ai_work_orders.spurious}/{wa.ai_work_orders.dropped}</div>
                <div className="s">truly missing requests that still got a suggestion</div></div>
            )}
            {h0 && (
              <div><div className="k">Messy column names</div><div className="v">{h0.correct} → {h1 ? h1.correct : "—"}<small style={{ fontSize: 14 }}> /{h0.total}</small></div>
                <div className="s">rules alone ({h0.wrong} wrong) → with AI suggestions ({h1?.wrong ?? 0} wrong, never auto-applied)</div></div>
            )}
            <div><div className="k">Speed</div><div className="v">{Math.round((wa ?? ro).ms_per_audit)}<small style={{ fontSize: 14 }}> ms</small></div>
              <div className="s">per full audit of ~1,600 rows on a laptop CPU{wa ? ", AI included" : ""}</div></div>
          </div>
          <p className="muted" style={{ fontSize: 13, marginTop: 10 }}>
            Synthetic benchmark from {new Date(ev!.generated_at).toLocaleDateString()} · reproduce it with{" "}
            <span className="mono">python -m eval.harness</span>. We compared four free local models; no small model
            reliably tells "kitchen tap dripping" (the same issue) from "leak under bathroom sink" (a different one) on
            wording alone. So AI only ranks candidates inside the same property and a 30-day window, and a person decides.
          </p>
        </>
      )}
    </section>
  );
}

const PIPE = String.raw`
 legacy export            consolidated import
 ┌───────────────┐        ┌───────────────┐
 │ BV-L-1042     │        │      ∅        │   missing lease
 │ Unit 4B       │───?───▶│               │   −$2,250/mo
 │ $2,250.00     │        │               │
 └───────────────┘        └───────────────┘
 ┌───────────────┐        ┌───────────────┐
 │ BV-L-1082     │═══════▶│ CPM-L-50080   │   re-keyed ✓
 │ $2,500.00     │   ≠    │ $2,050.00     │   −$450/mo
 └───────────────┘        └───────────────┘`;

export function About() {
  return (
    <div className="page">
      <Kicker sec="07">About &amp; pitch</Kicker>
      <h1 className="page-title">Moving data is not the same as <em>proving it survived.</em></h1>
      <div className="grid split section">
        <div className="prose">
          <p>
            Every acquisition ends with a migration: the acquired company’s tenants, leases, deposits and work orders are
            exported from one system and imported into another. Migration tools move rows. Nobody proves, row by row,
            that the business survived — that every active lease still exists, bills the same rent, sits on the right
            unit, and that every deposit owed to a tenant is still on the books.
          </p>
          <p className="pull">MergeProof is the verification layer for business integrations.</p>
          <p>
            It compares the before and after exports, pairs records even when IDs and formats change, and tests the
            invariants a property manager actually cares about. Every finding carries the source file and row on both
            sides, a reproducible check, a defensible calculation and a suggested next action — and the reviewer’s
            decision is kept in an audit trail.
          </p>

          <h3>Who it’s for first</h3>
          <p>
            Independent property managers and small management groups (10–100 people) that buy a smaller operator and
            consolidate its rent roll. The buyer is the owner, finance/operations lead or integration manager who needs
            confidence before the next rent cycle. PE-backed consolidators and their operating partners are a second
            channel.
          </p>

          <h3>What makes it different</h3>
          <ul>
            <li>Relationship-aware: tenant → lease → unit → property, not a CSV diff.</li>
            <li>Distinguishes harmless change (new IDs, “Avenue” → “Ave.”, “$2,250.00” vs “2250”) from financially important change.</li>
            <li>Conservative by design: ambiguous matches go to a human; two “Alex Kim” records never collapse on name alone.</li>
            <li>Money that holds up: rent at risk is deduplicated per lease, labelled an estimate, and deposits are kept separate as a liability.</li>
            <li>Deterministic and explainable — no model invents differences; a local model only suggests, and its results are measured.</li>
            <li>Before <i>and</i> after: plan the import against the target’s template, rehearse it in a dry run that predicts findings with the
              same rules, sign off one exact plan hash, then verify what actually loaded — with a tamper-evident release receipt.</li>
          </ul>

          <h3>Why now</h3>
          <p>
            Business-critical records are spread across more vertical SaaS systems than ever, and every acquisition,
            software replacement and platform consolidation creates the same continuity-verification problem.
          </p>

          <h3>Where it can go</h3>
          <p>
            The same matching, provenance and rule engine applies to insurance book transfers, clinic administration,
            field-service customer lists, billing migrations and, later, ERP/CRM consolidations. Property management is
            the wedge; verification of business-system transitions is the category.
          </p>

          <h3>Defensibility over time</h3>
          <ul>
            <li>Entity-matching quality and vertical validation rules that improve with every audit.</li>
            <li>Schemas learned from lawfully obtained, customer-authorized exports.</li>
            <li>Source-to-target evidence trails, reviewer feedback and audit history customers rely on.</li>
            <li>Integrations and trust — not a wrapper around a general-purpose model.</li>
          </ul>

          <AiSection />

          <h3>Business model — hypotheses to test</h3>
          <p>
            Start with a paid migration audit or pilot, then recurring monitoring and enterprise plans. Pricing hypotheses
            to validate in customer interviews (not current prices): roughly $250–$1,000 per migration audit for small
            operators; custom engagements for consolidators. No customers or revenue are claimed here.
          </p>

          <h3>First ten customers</h3>
          <p>
            Offer a free 20-minute migration check to property managers that recently acquired a portfolio or switched
            software. Ask for sample or redacted export formats; collect real data only with permission and adequate
            security. Build the outreach list from independently verified public acquisition announcements and
            authorized personal research — never by republishing or automating restricted databases.
          </p>

          <h3>Honest limits of this demo</h3>
          <ul>
            <li>All sample companies, people, addresses and amounts are synthetic. Sample findings are demo outcomes, not industry loss rates.</li>
            <li>Audits exports you provide; no AppFolio, Buildium, Yardi or other vendor connection.</li>
            <li>Workspaces are separated per browser for the demo, not secured — real customer data needs access controls, tenant separation, a retention policy and a security review first.</li>
            <li>No PitchBook or WRDS data integration.</li>
            <li>The Cedar import template is fictional, and the dry run models a strict importer — a real destination’s importer may differ.
              Sign-off is single-reviewer; the release receipt records checks, it is not an audit opinion.</li>
          </ul>
        </div>
        <aside className="stack about-aside">
          <div className="card pad ticks">
            <Kicker sec="◇">The indispensable moment</Kicker>
            <pre className="ascii-art" aria-label="Diagram: a missing lease and a changed rent between exports">{PIPE}</pre>
            <p className="muted" style={{ fontSize: 13.5, marginTop: 10 }}>
              Click a flagged lease and the app proves — with both source rows and the arithmetic — exactly what may have
              been lost in the move.
            </p>
            <div className="btn-row" style={{ marginTop: 14 }}>
              <Link to="/" className="btn primary">Try it on the sample</Link>
            </div>
          </div>
          <div className="card pad">
            <Kicker sec="⚙">Under the hood</Kicker>
            <p className="muted" style={{ fontSize: 13.5 }}>
              FastAPI + SQLite (parameterized), Decimal money in integer cents, RapidFuzz and a local model2vec
              embedding model for suggestions only, openpyxl for XLSX, ReportLab for the PDF. React + TypeScript front
              end. A ground-truth evaluation harness gates every change. Every number on screen is computed from the
              stored records.
            </p>
          </div>
        </aside>
      </div>
    </div>
  );
}
