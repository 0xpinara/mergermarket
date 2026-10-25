import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { api, type PlanCandidate, type Transform } from "../../lib/api";
import { useAppState } from "../../lib/store";
import { num, when } from "../../lib/format";
import { AsciiBar, Drawer, ErrorBox, Kicker, Loading, useToast } from "../ui";
import { CONFIDENCE, ItemChip, TYPE_GLYPH, Ticks, describe, useInvalidateInteg } from "./shared";

const SIGNAL: Record<string, string> = {
  audit: "AUDIT MAP", name: "NAME", references: "REFERENCES", ai: "LOCAL AI", template: "TEMPLATE",
};

function Cand({ c, current, onUse, busy }: { c: PlanCandidate; current: boolean; onUse: () => void; busy: boolean }) {
  return (
    <li className={`cand2${current ? " current" : ""}`}>
      <div className="ch">
        <span className="mono b">{c.sources.join(" + ") || "constant"}</span>
        <span className="score mono">{Math.round(c.score * 100)}</span>
        <AsciiBar fraction={c.score} width={16} />
        <span className="grow" />
        {current ? <span className="chip tone-ink">selected</span> : (
          <button className="btn sm" onClick={onUse} disabled={busy}>Use this</button>
        )}
      </div>
      <ul className="sigs">
        {c.signals.map((s, k) => (
          <li key={k} className={`sig sig-${s.kind}${s.kind === "references" && (s.ratio ?? 1) < 0.5 ? " bad" : ""}`}>
            <span className="tag">{SIGNAL[s.kind] ?? s.kind}</span>
            <span className="pts mono">+{Math.round(s.points * 100)}</span>
            <span className="d"><Ticks text={s.detail} /></span>
          </li>
        ))}
      </ul>
      <div className="muted small"><Ticks text={describe(c.transform, c.sources)} /></div>
    </li>
  );
}

export function FieldDrawer({ dealId, itemId, onClose }: { dealId: string; itemId: string; onClose: () => void }) {
  const { reviewer } = useAppState();
  const toast = useToast();
  const invalidate = useInvalidateInteg(dealId);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["integ", dealId, "evidence", itemId], queryFn: () => api.integ.evidence(dealId, itemId) });
  const act = useMutation({
    mutationFn: (body: { action: string; sources?: string[]; transform?: Transform }) =>
      api.integ.act(dealId, itemId, { ...body, reviewer, note: note.trim() || null }),
    onSuccess: (_p, v) => {
      setErr(null);
      setNote("");
      toast(`${itemId}: ${v.action === "edit" ? "source chosen and approved" : v.action + "d"}`.replace("reopend", "reopened"));
      invalidate();
    },
    onError: (e: Error) => setErr(e.message),
  });

  const e = q.data;
  const i = e?.item;
  const decided = i && (i.status === "approved" || i.status === "ignored" || i.status === "rejected");
  return (
    <Drawer label={`Mapping evidence for ${itemId}`} onClose={onClose}>
      <div className="drawer-head">
        <div>
          <Kicker plain>Mapping evidence · {e?.source_file ?? "…"}</Kicker>
          <h2 className="mono" style={{ fontSize: 22 }}>{itemId}</h2>
        </div>
        <button className="icon-btn drawer-close" onClick={onClose} aria-label="Close"><X /></button>
      </div>
      <div className="drawer-body">
        {q.isLoading && <Loading />}
        {q.error && <ErrorBox error={q.error} onRetry={() => q.refetch()} />}
        {e && i && (
          <>
            <section className="drawer-section">
              <div className="btn-row">
                <ItemChip status={i.status} />
                <span className="chip tone-muted">{TYPE_GLYPH[i.type]} {i.type}</span>
                {i.required && <span className="chip tone-coral">required</span>}
                {i.financial && <span className="chip tone-amber">$ financial · approve individually</span>}
                <span className={`conf ${CONFIDENCE[i.confidence].cls}`}>{CONFIDENCE[i.confidence].label}</span>
              </div>
              {e.target.description && <p className="muted" style={{ marginTop: 8 }}>{e.target.description}</p>}
              <div className="flowline mono">
                <span>{i.sources.join(" + ") || (i.transform?.op === "constant" ? "constant" : "∅")}</span>
                <span className="wire" aria-hidden="true"> ──{i.transform?.op ?? "?"}──▶ </span>
                <span className="b">{i.column}</span>
              </div>
              <p className="small"><Ticks text={describe(i.transform, i.sources)} /></p>
              {i.decision && (
                <p className="small muted">
                  {i.decision.action.replace("_", " ")} by <b>{i.decision.by}</b> · {when(i.decision.at)}
                  {i.decision.note && <> — “{i.decision.note}”</>}
                </p>
              )}
            </section>

            {(i.reasons.length > 0 || e.live_warnings.length > 0) && (
              <section className="drawer-section">
                <h4 className="sh4">Why</h4>
                {e.live_warnings.map((w, k) => <p key={"w" + k} className="errline"><span aria-hidden="true">✕</span><span><Ticks text={w} /></span></p>)}
                {i.reasons.map((r, k) => <p key={k} className={i.status === "needs_review" ? "warnline" : "small"}><span aria-hidden="true">{i.status === "needs_review" ? "!" : "·"}</span> <span><Ticks text={r} /></span></p>)}
                {i.warnings.map((r, k) => <p key={"x" + k} className="warnline"><span aria-hidden="true">!</span><span>{r}</span></p>)}
              </section>
            )}

            {i.enum_coverage && (
              <section className="drawer-section">
                <h4 className="sh4">Code list</h4>
                <table className="map-table">
                  <thead><tr><th>Legacy value</th><th>Canonical</th><th>Target</th><th>Rows</th></tr></thead>
                  <tbody>
                    {Object.entries(i.enum_coverage.mapped).map(([v, m]) => (
                      <tr key={v}><td className="mono">{v}</td><td className="mono muted">{m.canonical}</td><td className="mono">{m.target}</td><td className="mono">{num(m.rows)}</td></tr>
                    ))}
                    {Object.entries(i.enum_coverage.unmapped).map(([v, m]) => (
                      <tr key={v} className="bad"><td className="mono">{v}</td><td className="mono muted">{m.canonical ?? "?"}</td><td className="errline">no equivalent</td><td className="mono">{num(m.rows)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}

            <section className="drawer-section">
              <h4 className="sh4">Candidates <span className="muted small">— evidence points, strongest first</span></h4>
              <ul className="cands2">
                {i.candidates.map((c) => (
                  <Cand key={c.sources.join("+") || "const"} c={c} busy={act.isPending}
                        current={JSON.stringify(c.sources) === JSON.stringify(i.sources) && i.status !== "rejected"}
                        onUse={() => act.mutate({ action: "edit", sources: c.sources, transform: c.transform })} />
                ))}
              </ul>
              {i.excluded.length > 0 && (
                <>
                  <p className="small muted" style={{ marginTop: 10 }}>Ruled out by type guards:</p>
                  <ul className="excl">
                    {i.excluded.map((x) => <li key={x.sources.join("+")} className="small"><span className="mono">{x.sources.join(" + ")}</span> — {x.reason}</li>)}
                  </ul>
                </>
              )}
            </section>

            {e.profile.length > 0 && (
              <section className="drawer-section">
                <h4 className="sh4">What those columns hold</h4>
                <table className="map-table">
                  <thead><tr><th>Column</th><th>Type</th><th>Unique</th><th>Blank</th><th>Shape</th><th>Examples</th></tr></thead>
                  <tbody>
                    {e.profile.map((c) => (
                      <tr key={c.column}>
                        <td className="mono">{c.column}</td><td className="mono">{c.type}</td>
                        <td className="mono">{c.unique ? "yes" : "no"}</td><td className="mono">{num(c.blank)}</td>
                        <td className="mono muted">{c.patterns[0]?.shape}</td>
                        <td className="sample">{c.pii ? <span className="pii">hidden</span> : (c.examples ?? []).join(" · ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}

            <section className="drawer-section review-box">
              <h4 className="sh4">Decide as {reviewer || "—"}</h4>
              <label className="field">
                <span>Note {e.needs_note && !decided ? <b className="req">— required: this mapping is contested</b> : <span className="muted">(optional)</span>}</span>
                <textarea className="inp" rows={2} maxLength={1000} value={note} onChange={(ev) => setNote(ev.target.value)}
                          placeholder="Why is this the right source? Kept in the decision log." />
              </label>
              {err && <p className="errline" role="alert"><span aria-hidden="true">✕</span><span>{err}</span></p>}
              <div className="btn-row">
                {!decided && i.transform && (
                  <button className="btn primary" disabled={act.isPending || !reviewer.trim()} onClick={() => act.mutate({ action: "approve" })}>
                    Approve {i.sources.join(" + ") || "constant"}
                  </button>
                )}
                {!decided && <button className="btn ghost" disabled={act.isPending} onClick={() => act.mutate({ action: "reject" })}>Reject suggestion</button>}
                {!i.required && i.status !== "ignored" && (
                  <button className="btn ghost" disabled={act.isPending} onClick={() => act.mutate({ action: "ignore" })}>Leave empty…</button>
                )}
                {decided && <button className="btn" disabled={act.isPending} onClick={() => act.mutate({ action: "reopen" })}>Reopen</button>}
              </div>
              <p className="muted small">Decisions change the plan hash only when they change what is mapped. Every action is logged.</p>
            </section>

            {e.history.length > 0 && (
              <section className="drawer-section">
                <h4 className="sh4">History</h4>
                <ol className="timeline tight">
                  {e.history.map((h) => (
                    <li key={h.id}><span className="when mono">{when(h.created_at)}</span>
                      <span><b>{h.actor}</b> {h.action.replace("_", " ")}{h.note && <> — “{h.note}”</>} <span className="mono muted small">v{h.version}</span></span></li>
                  ))}
                </ol>
              </section>
            )}
          </>
        )}
      </div>
    </Drawer>
  );
}
