import { useMutation, useQuery } from "@tanstack/react-query";
import { api, type Plan, type PlanItem } from "../../lib/api";
import { useAppState } from "../../lib/store";
import { num, when } from "../../lib/format";
import { AsciiBar, Empty, useToast } from "../ui";
import { CONFIDENCE, ItemChip, TYPE_GLYPH, Ticks, decisionOrder, describe, itemNeedsDecision, useInvalidateInteg } from "./shared";

function TargetCell({ i }: { i: PlanItem }) {
  return (
    <span className="tcol">
      <span className="tg" title={i.type} aria-hidden="true">{TYPE_GLYPH[i.type] ?? "·"}</span>
      <span className="mono">{i.column}</span>
      {i.required && <span className="req" title="Required by the template">*</span>}
      {i.financial && <span className="fin" title="Financial — approved one by one">$</span>}
    </span>
  );
}

function Row({ i, onOpen }: { i: PlanItem; onOpen: (id: string) => void }) {
  const warn = i.live_warnings.length > 0;
  return (
    <tr className={`irow s-${i.status}${warn ? " warn" : ""}`}>
      <td><TargetCell i={i} /></td>
      <td className="mono src">
        {i.sources.length ? i.sources.join(" + ") : i.transform?.op === "constant" ? <span className="muted">constant</span> : <span className="muted">—</span>}
      </td>
      <td className="xf"><Ticks text={describe(i.transform, i.sources)} /></td>
      <td><span className={`conf ${CONFIDENCE[i.confidence].cls}`} title={CONFIDENCE[i.confidence].hint}>{CONFIDENCE[i.confidence].label}</span></td>
      <td>
        <ItemChip status={i.status} />
        {warn && <span className="chip tone-coral" title={i.live_warnings[0]}>✕ breaks refs</span>}
      </td>
      <td className="act">
        <button className="btn sm ghost" onClick={() => onOpen(i.id)} aria-label={`Evidence for ${i.id}`}>Evidence →</button>
      </td>
    </tr>
  );
}

export function MapTables({ plan, onOpen }: { plan: Plan; onOpen: (id: string) => void }) {
  return (
    <div className="stack">
      {plan.template_tables.map((t) => {
        const b = plan.tables.find((x) => x.name === t.name)!;
        const items = plan.items.filter((i) => i.table === t.name);
        const done = items.filter((i) => !itemNeedsDecision(i)).length;
        return (
          <section key={t.name} className="card">
            <div className="card-head maphead">
              <h3>
                <span className="mono">{t.file}</span>
              </h3>
              <span className="from mono">
                <span aria-hidden="true">◀━━ </span>
                {b.source_file ? <>{b.source_file} <span className="muted">· {num(b.rows)} rows</span></> : <span className="errline">no legacy export</span>}
              </span>
              <span className="muted small">{done}/{items.length} decided</span>
            </div>
            <div className="table-wrap">
              <table className="map-table plan-table">
                <thead>
                  <tr><th>Target column</th><th>Legacy source</th><th>Transform</th><th>Evidence</th><th>Status</th><th><span className="sr-only">Open</span></th></tr>
                </thead>
                <tbody>{items.map((i) => <Row key={i.id} i={i} onOpen={onOpen} />)}</tbody>
              </table>
            </div>
            {b.unused.length > 0 && <p className="warnline" style={{ padding: "8px 20px" }}>Also uploaded but not used: {b.unused.join(", ")}</p>}
          </section>
        );
      })}
    </div>
  );
}

const ACTION_WORD: Record<string, string> = {
  created: "created the plan", regenerated: "rebuilt suggestions", approve: "approved", bulk_approve: "bulk-approved",
  reject: "rejected", edit: "chose a source for", ignore: "ignored", reopen: "reopened", rebased: "rebased on new exports",
  dry_run: "ran a dry run", signoff: "signed off", signoff_revoked: "revoked sign-off", exported: "exported the package",
  release_receipt: "issued a release receipt",
};

export function DecisionLog({ dealId }: { dealId: string }) {
  const q = useQuery({ queryKey: ["integ", dealId, "events"], queryFn: () => api.integ.events(dealId) });
  const ev = q.data ?? [];
  return (
    <section className="card">
      <div className="card-head"><h3>Decision log</h3><span className="muted small">append-only · {ev.length} entries</span></div>
      <ol className="timeline tight">
        {ev.slice(0, 40).map((e) => (
          <li key={e.id}>
            <span className="when mono">{when(e.created_at)}</span>
            <span>
              <b>{e.actor}</b> {ACTION_WORD[e.action] ?? e.action}
              {e.item_id && <> <span className="mono">{e.item_id}</span></>}
              {e.before && e.after && JSON.stringify(e.before.sources) !== JSON.stringify(e.after.sources) && (
                <span className="mono muted"> · {(e.before.sources || []).join("+") || "∅"} → {(e.after.sources || []).join("+") || "∅"}</span>
              )}
              {e.note && <span className="note"> — “{e.note}”</span>}
              <span className="mono muted small"> · v{e.version}</span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function ReviewQueue({ dealId, plan, onOpen }: { dealId: string; plan: Plan; onOpen: (id: string) => void }) {
  const { reviewer } = useAppState();
  const toast = useToast();
  const invalidate = useInvalidateInteg(dealId);
  const bulk = useMutation({
    mutationFn: () => api.integ.bulk(dealId, reviewer),
    onSuccess: (r) => { toast(`Approved ${r.approved.length} exact, non-financial columns`); invalidate(); },
    onError: (e: Error) => toast(e.message, "err"),
  });
  const approve = useMutation({
    mutationFn: (id: string) => api.integ.act(dealId, id, { action: "approve", reviewer }),
    onSuccess: () => invalidate(),
    onError: (e: Error) => toast(e.message, "err"),
  });
  const c = plan.counts;
  const queue = plan.items.filter(itemNeedsDecision).sort((a, b) => decisionOrder(a) - decisionOrder(b));
  return (
    <div className="stack">
      <div className="grid split review-top">
        <div className="card pad">
          <div className="k mono small muted">DECIDED</div>
          <div className="big mono">{c.decided}<small>/{c.total}</small></div>
          <AsciiBar fraction={c.decided / Math.max(1, c.total)} width={32} />
          <p className="muted small" style={{ marginTop: 8 }}>
            {c.needs_review} contested · {c.financial_pending} financial pending · {c.required_open} required open
          </p>
        </div>
        <div className="card pad">
          <h4 className="serif" style={{ fontSize: 22 }}>Approve the obvious ones together</h4>
          <p className="muted small" style={{ margin: "6px 0 12px" }}>
            {c.bulk_eligible} column{c.bulk_eligible === 1 ? "" : "s"} are bound by the audit’s own mapping with nothing
            contradicting them. Rent, deposits, lease status and anything contested are never bulk-approved.
          </p>
          <button className="btn primary" disabled={!c.bulk_eligible || bulk.isPending || !reviewer.trim()}
                  onClick={() => bulk.mutate()}>
            Approve {c.bulk_eligible} as {reviewer || "…"}
          </button>
        </div>
      </div>
      {queue.length === 0 ? (
        <Empty art={"  ┌──────────┐\n  │ ✓ ✓ ✓ ✓  │\n  └──────────┘"} title="Every column has a decision">
          Run the dry run to see exactly what the import would do.
        </Empty>
      ) : (
        <section className="card">
          <div className="card-head"><h3>Needs a person</h3><span className="muted small">{queue.length} left · contested first</span></div>
          <ul className="queue">
            {queue.map((i) => (
              <li key={i.id} className={`q-${i.status}`}>
                <div className="qh">
                  <span className="mono b">{i.id}</span>
                  <ItemChip status={i.status} />
                  {i.financial && <span className="chip tone-amber">$ financial</span>}
                  <span className="grow" />
                  {!i.needs_note && i.transform && (
                    <button className="btn sm" disabled={approve.isPending || !reviewer.trim()} onClick={() => approve.mutate(i.id)}>
                      Approve
                    </button>
                  )}
                  <button className="btn sm ink" onClick={() => onOpen(i.id)}>Decide →</button>
                </div>
                <div className="qb">
                  <span className="mono">{i.sources.join(" + ") || "—"}</span>
                  <span className="arrow" aria-hidden="true"> ──▶ </span>
                  <span className="muted"><Ticks text={describe(i.transform, i.sources)} /></span>
                </div>
                {(i.live_warnings[0] || i.reasons[0]) && (
                  <p className={i.live_warnings[0] ? "errline" : "warnline"}>
                    <span aria-hidden="true">{i.live_warnings[0] ? "✕" : "!"}</span>
                    <span><Ticks text={i.live_warnings[0] || i.reasons[0]} /></span>
                  </p>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      <DecisionLog dealId={dealId} />
    </div>
  );
}
