import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Download, Search } from "lucide-react";
import { api, downloadUrl, type FindingRow, type Severity } from "../lib/api";
import { ENTITY_LABEL, SEVERITY_ORDER, money, plural } from "../lib/format";
import { Empty, ErrorBox, Kicker, Loading, SevChip, StatusPill } from "../components/ui";
import { EvidenceDrawer } from "../components/EvidenceDrawer";
import { useRouteDeal } from "./Workspace";

type StatusFilter = "open" | "closed" | "all" | "gone";
type MoneyFilter = "any" | "rent" | "deposit" | "money" | "material";

function Effect({ f }: { f: FindingRow }) {
  if (f.rent_counted_cents > 0 && (f.status === "open" || f.status === "investigating" || f.needs_rereview))
    return <span style={{ color: "var(--coral-ink)" }}>−{money(f.rent_counted_cents)}/mo</span>;
  if (f.rent_risk_cents > 0 && f.rent_covered_by)
    return <span className="muted" title={`Counted once under ${f.rent_covered_by}`}>−{money(f.rent_risk_cents)} · counted once</span>;
  if (f.rent_risk_cents > 0) return <span className="muted">−{money(f.rent_risk_cents)}/mo · closed</span>;
  if (f.deposit_cents > 0) return <span style={{ color: "var(--amber-ink)" }}>{money(f.deposit_cents)} deposit</span>;
  if (f.pending_rent_cents > 0) return <span className="muted">{money(f.pending_rent_cents)}/mo in review</span>;
  if (f.impact_type === "rent_increase") return <span className="muted">+{money(f.impact_cents)}/mo (not risk)</span>;
  return <span className="muted">—</span>;
}

export function Findings() {
  const id = useRouteDeal();
  const [params, setParams] = useSearchParams();
  const open = params.get("f");
  const [q, setQ] = useState("");
  const [sev, setSev] = useState<Severity | "all">("all");
  const [entity, setEntity] = useState("all");
  const [status, setStatus] = useState<StatusFilter>("open");
  const [moneyF, setMoneyF] = useState<MoneyFilter>("any");
  const [rule, setRule] = useState("all");

  const fq = useQuery({ queryKey: ["findings", id, "all"], queryFn: () => api.findings(id, true) });
  const sq = useQuery({ queryKey: ["summary", id], queryFn: () => api.summary(id) });
  const all = fq.data ?? [];
  const active = all.filter((f) => f.active);

  const rules = useMemo(() => {
    const m = new Map<string, string>();
    for (const f of all) m.set(f.rule_id, f.rule_name || f.title);
    return [...m.entries()].sort();
  }, [all]);
  const entities = useMemo(() => [...new Set(all.map((f) => f.entity))].sort(), [all]);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return all
      .filter((f) => {
        if (status === "gone") return !f.active;
        if (!f.active) return false;
        const isOpen = f.status === "open" || f.status === "investigating" || f.needs_rereview;
        if (status === "open" && !isOpen) return false;
        if (status === "closed" && isOpen) return false;
        if (sev !== "all" && f.severity !== sev) return false;
        if (entity !== "all" && f.entity !== entity) return false;
        if (rule !== "all" && f.rule_id !== rule) return false;
        if (moneyF === "rent" && !(f.rent_risk_cents > 0)) return false;
        if (moneyF === "deposit" && !(f.deposit_cents > 0)) return false;
        if (moneyF === "money" && !(f.rent_risk_cents || f.deposit_cents || f.pending_rent_cents)) return false;
        if (moneyF === "material" && !f.material) return false;
        if (needle) {
          const hay = [f.title, f.subject, f.rule_id, f.id, f.explanation, f.source_ref?.id, f.target_ref?.id,
            f.source_ref?.file, f.target_ref?.file].join(" ").toLowerCase();
          if (!hay.includes(needle)) return false;
        }
        return true;
      })
      .sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] ||
        (b.rent_risk_cents + b.deposit_cents + b.pending_rent_cents) - (a.rent_risk_cents + a.deposit_cents + a.pending_rent_cents) ||
        a.rule_id.localeCompare(b.rule_id));
  }, [all, q, sev, entity, status, moneyF, rule]);

  const counts = useMemo(() => {
    const isOpen = (f: FindingRow) => f.status === "open" || f.status === "investigating" || f.needs_rereview;
    const scope = active.filter((f) => status === "all" || (status === "open" ? isOpen(f) : status === "closed" ? !isOpen(f) : false));
    const c: Record<string, number> = { all: scope.length, critical: 0, high: 0, medium: 0, low: 0 };
    for (const f of scope) c[f.severity] += 1;
    return c;
  }, [active, status]);

  const openDrawer = (fid: string) => setParams((p) => { p.set("f", fid); return p; });
  const closeDrawer = () => setParams((p) => { p.delete("f"); return p; });
  const s = sq.data;
  const k = s?.kpis;

  if (fq.isLoading || sq.isLoading) return <div className="page"><Loading label="Loading findings" /></div>;
  if (fq.error) return <div className="page"><ErrorBox error={fq.error} onRetry={() => fq.refetch()} /></div>;

  if (!s?.has_run) {
    return (
      <div className="page">
        <Kicker sec="04">Findings inbox</Kicker>
        <Empty art={" ┌──────────┐\n │ ░░░░░░░░ │\n │ ░ no run │\n └──────────┘"} title="No audit run yet"
               actions={<Link className="btn primary" to={`/deals/${id}/workspace`}>Go to audit workspace</Link>}>
          Run the continuity audit to see what survived the move — and what didn’t.
        </Empty>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <Kicker sec="04">Findings inbox</Kicker>
          <h1 className="page-title">
            {k!.findings_open ? <>{plural(k!.findings_open, "finding")} <em>to review</em></> : <>All findings <em>closed</em></>}
          </h1>
          <p className="page-sub">
            Each one is backed by the source row, a reproducible check and a suggested next action. Est. rent at risk{" "}
            <b className="mono" style={{ color: "var(--coral-ink)" }}>{money(k!.rent_at_risk_open)}/mo</b> · deposits{" "}
            <b className="mono" style={{ color: "var(--amber-ink)" }}>{money(k!.deposit_open)}</b> (separate) ·{" "}
            {k!.findings_closed} closed.
            {s.materiality && (s.materiality.rent_cents !== null || s.materiality.deposit_cents !== null) && (
              <> {k!.material_open ?? 0} open meet the deal’s materiality thresholds
                ({s.materiality.rent_cents !== null ? `rent ≥ ${money(s.materiality.rent_cents)}/mo` : ""}
                {s.materiality.rent_cents !== null && s.materiality.deposit_cents !== null ? ", " : ""}
                {s.materiality.deposit_cents !== null ? `deposit ≥ ${money(s.materiality.deposit_cents)}` : ""}) — flags only.</>
            )}
          </p>
        </div>
        <div className="btn-row">
          <a className="btn ghost sm" href={downloadUrl(`/deals/${id}/findings.csv`)} download>
            <Download /> Findings CSV
          </a>
          <Link className="btn ink sm" to={`/deals/${id}/proof`}>Continuity proof →</Link>
        </div>
      </div>

      {s.run?.stale && (
        <div className="banner">
          <span className="ico">↻</span>
          <span className="grow">
            {s.run.match_decisions_pending
              ? `${plural(s.run.match_decisions_pending, "confirmed match", "confirmed matches")} not yet applied to downstream checks.`
              : "Exports or mappings changed since this run."}{" "}
            Rerun to refresh — your review decisions are kept.
          </span>
          <Link to={`/deals/${id}/workspace`} className="btn sm ink">Rerun audit</Link>
        </div>
      )}
      {k!.needs_rereview > 0 && (
        <div className="banner">
          <span className="ico">!</span>
          <span className="grow">{plural(k!.needs_rereview, "reviewed finding")} had its evidence change on the last run and needs re-review.</span>
        </div>
      )}

      <div className="filters" role="search">
        <div className="search">
          <Search aria-hidden="true" />
          <input aria-label="Search findings" placeholder="Search tenant, unit, rule, ID or file…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="seg" role="group" aria-label="Severity">
          {(["all", "critical", "high", "medium", "low"] as const).map((v) => (
            <button key={v} aria-pressed={sev === v} onClick={() => setSev(v)}>
              {v === "all" ? "All" : v === "critical" ? "Crit" : v === "medium" ? "Med" : v[0].toUpperCase() + v.slice(1)}
              <span className="c">{counts[v]}</span>
            </button>
          ))}
        </div>
        <select className="sel" aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value as StatusFilter)}>
          <option value="open">Open &amp; investigating</option>
          <option value="closed">Reviewed (expected / resolved)</option>
          <option value="all">All current</option>
          <option value="gone">No longer detected</option>
        </select>
        <select className="sel" aria-label="Record type" value={entity} onChange={(e) => setEntity(e.target.value)}>
          <option value="all">All record types</option>
          {entities.map((e) => <option key={e} value={e}>{ENTITY_LABEL[e] ?? e}</option>)}
        </select>
        <select className="sel" aria-label="Money" value={moneyF} onChange={(e) => setMoneyF(e.target.value as MoneyFilter)}>
          <option value="any">Any impact</option>
          <option value="money">Has money at stake</option>
          <option value="rent">Rent at risk</option>
          <option value="deposit">Deposit discrepancy</option>
          <option value="material">Material (meets thresholds)</option>
        </select>
        <select className="sel" aria-label="Rule" value={rule} onChange={(e) => setRule(e.target.value)}>
          <option value="all">All rules</option>
          {rules.map(([r, n]) => <option key={r} value={r}>{r} · {n}</option>)}
        </select>
      </div>

      {rows.length === 0 ? (
        <Empty title={status === "open" && !q && sev === "all" ? "Nothing open" : "No findings match"}
               actions={<button className="btn ghost" onClick={() => { setQ(""); setSev("all"); setEntity("all"); setMoneyF("any"); setRule("all"); setStatus("all"); }}>Clear filters</button>}>
          {status === "open" ? "Every current finding has been reviewed, or your filters hide them." : "Try widening the filters."}
        </Empty>
      ) : (
        <div className="card table-wrap">
          <table className="t stack-sm">
            <thead>
              <tr>
                <th style={{ width: 70 }}>Severity</th>
                <th>Finding</th>
                <th>Affected</th>
                <th className="num">Monthly effect</th>
                <th>Evidence</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((f) => {
                const ref = f.source_ref ?? f.target_ref;
                return (
                  <tr key={f.id} className={`clickable${open === f.id ? " selected" : ""}${f.active ? "" : " inactive"}`}
                      onClick={() => openDrawer(f.id)}>
                    <td className="c-sev"><SevChip severity={f.severity} /></td>
                    <td>
                      <button className="row-title" onClick={(e) => { e.stopPropagation(); openDrawer(f.id); }}>{f.title}</button>
                      <span className="rid">{f.rule_id} · {f.id}{f.confidence === "needs_review" ? " · needs review" : ""}
                        {f.material && <span className="mat" title={`Material: ${f.material_reason}. A flag only — nothing is hidden.`}> · material</span>}</span>
                    </td>
                    <td className="c-affected" style={{ maxWidth: 320 }}>{f.subject}</td>
                    <td className="num"><Effect f={f} /></td>
                    <td className="ref c-ev">
                      {ref ? <>{ref.file}<span style={{ color: "var(--emerald)" }}>:r{ref.row}</span></> : "—"}
                      {f.source_ref && f.target_ref && <span style={{ display: "block" }}>↔ {f.target_ref.file}:r{f.target_ref.row}</span>}
                    </td>
                    <td className="c-status"><StatusPill status={f.status} rereview={f.needs_rereview} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted mono" style={{ fontSize: 11.5, marginTop: 10 }}>
        Showing {rows.length} of {active.length} current findings · engine {s.run?.engine_version} · run {s.run?.id}
      </p>
      {open && <EvidenceDrawer findingId={open} onClose={closeDrawer} onOpen={openDrawer} />}
    </div>
  );
}
