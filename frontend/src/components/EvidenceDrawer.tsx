import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { api, type Candidate, type CompareRow, type EvidenceRecord, type FindingDetail } from "../lib/api";
import { ENTITY_LABEL, STATUS_LABEL, money, when } from "../lib/format";
import { useAppState } from "../lib/store";
import { AsciiBar, Drawer, ErrorBox, Kicker, Loading, SevChip, StatusPill, useToast } from "./ui";

const MATCH_LABEL: Record<string, { label: string; tone: string }> = {
  confirmed: { label: "Confirmed match", tone: "lime" },
  probable: { label: "Probable match", tone: "lime" },
  reviewer_confirmed: { label: "Confirmed by reviewer", tone: "teal" },
  needs_review: { label: "Needs review", tone: "amber" },
  unmatched: { label: "No confident match", tone: "coral" },
};

const ACTION_DONE: Record<string, string> = {
  investigate: "Marked as investigating",
  expected_exception: "Marked as expected exception",
  resolve: "Marked resolved",
  reopen: "Reopened — rerun the audit to re-evaluate",
  comment: "Comment added",
  confirm_match: "Match confirmed — rerun the audit to apply it to downstream checks",
};

function Ref({ rec }: { rec: EvidenceRecord | null }) {
  if (!rec) return null;
  return (
    <span className="src">
      {rec.file}
      <span className="row">:r{rec.row}</span>
      {rec.id ? <span style={{ color: "var(--ink-300)" }}> · {rec.id}</span> : null}
    </span>
  );
}

function Val({ v, note }: { v: CompareRow["before"]; note?: string | null }) {
  if (!v || (!v.raw && !v.value)) return <span className="muted">—{note && <span className="note">{note}</span>}</span>;
  const showNorm = v.value && v.raw && v.value !== v.raw;
  return (
    <>
      {v.raw ?? v.value}
      {note && <span className="note">{note}</span>}
      {showNorm && <span className="norm">= {v.value}</span>}
    </>
  );
}

const NOTE: Record<CompareRow["state"], string | null> = {
  same: null, format: "format only", rekeyed: "re-keyed", differs: "differs", missing: null, na: "not in export",
};

function Compare({ f }: { f: FindingDetail }) {
  const { payload: p, labels } = f;
  const rows = p.compare;
  if (!rows.length) return null;
  const noTarget = !p.target && rows.every((r) => r.state === "missing" || !r.after);
  const noSource = !p.source && rows.every((r) => !r.before);
  const searched = p.match?.notes?.[0];
  return (
    <div className="compare" role="table" aria-label="Before and after comparison">
      <div className="compare-head" role="row">
        <div role="columnheader" className="side">Field</div>
        <div role="columnheader">
          <div className="side"><b>Legacy</b> / expected<span className="sysname"> · {labels.old_system}</span></div>
          {p.source ? <Ref rec={p.source} /> : <span className="src" style={{ color: "var(--ink-400)" }}>no legacy record</span>}
        </div>
        <div role="columnheader">
          <div className="side"><b>Consolidated</b> / actual<span className="sysname"> · {labels.new_system}</span></div>
          {p.target ? <Ref rec={p.target} /> : <span className="src" style={{ color: "var(--ink-400)" }}>no matching record</span>}
        </div>
      </div>
      {noTarget || noSource ? (
        <div className="compare-missing" style={{ gridTemplateRows: `repeat(${rows.length}, auto)` }}>
          {rows.map((r, i) => (
            <div key={r.field} style={{ display: "contents" }} role="row">
              <div className="cm-f" style={{ gridColumn: 1, gridRow: i + 1 }} role="rowheader">{r.label}</div>
              <div className="cm-v" style={{ gridColumn: noSource ? 3 : 2, gridRow: i + 1 }} role="cell">
                <Val v={noSource ? r.after : r.before} />
              </div>
            </div>
          ))}
          <div className="cm-absent" style={{ gridColumn: noSource ? 2 : 3, gridRow: `1 / span ${rows.length}` }} role="cell">
            <div className="absent">
              <span className="glyph" aria-hidden="true">∅</span>
              <span className="t">{noSource ? `No record in ${labels.old}` : `No confident match in ${labels.new}`}</span>
              <span className="d">{noSource ? "This record exists only after migration." : searched}</span>
            </div>
          </div>
        </div>
      ) : (
        rows.map((r) => (
          <div className={`crow ${r.state}`} key={r.field} role="row">
            <div className="f" role="rowheader">{r.label}</div>
            <div className="v before" role="cell"><Val v={r.before} /></div>
            <div className="v after" role="cell"><Val v={r.after} note={NOTE[r.state]} /></div>
          </div>
        ))
      )}
    </div>
  );
}

function Receipt({ f }: { f: FindingDetail }) {
  const imp = f.payload.impact;
  if (!imp?.calc?.length) return null;
  // A decision whose evidence later changed counts as open again until it is re-reviewed.
  const closed = (f.status === "expected" || f.status === "resolved") && !f.needs_rereview;
  let verdict: { cls: string; text: string };
  if (imp.type === "rent_at_risk") {
    if (closed) verdict = { cls: "v-covered", text: `Excluded from open rent at risk — finding is ${STATUS_LABEL[f.status].toLowerCase()}.` };
    else if (f.rent_counted_cents > 0)
      verdict = { cls: "v-rent", text: `Counted in estimated monthly rent at risk: ${money(f.rent_counted_cents, { decimals: true })}/mo (requires review).` };
    else if (f.rent_covered_by)
      verdict = { cls: "v-covered", text: `Not added again — this lease's larger exposure is already counted under ${f.rent_covered_by}. Each lease counts once.` };
    else verdict = { cls: "v-covered", text: "Not counted." };
  } else if (imp.type === "deposit") {
    verdict = { cls: "v-deposit", text: "Tenant liability — tracked under deposit discrepancies. Never added to rent at risk." };
  } else if (imp.type === "rent_increase") {
    verdict = { cls: "v-info", text: "Higher rent is not counted as risk. Review for possible overbilling." };
  } else {
    verdict = { cls: "v-info", text: "Not counted as at-risk until a reviewer decides the match." };
  }
  const calc = imp.calc;
  return (
    <div className="receipt" aria-label="Calculation">
      <div className="ttl">{imp.label || "Calculation"}</div>
      {calc.slice(0, -1).map(([a, b]) => (
        <div className="line" key={a}><span>{a}</span><span>{b}</span></div>
      ))}
      <div className="rule" />
      <div className="line total"><span>{calc[calc.length - 1][0]}</span><span>{calc[calc.length - 1][1]}</span></div>
      <div className={`verdict ${verdict.cls}`}>{verdict.text}</div>
    </div>
  );
}

function MatchBox({ f }: { f: FindingDetail }) {
  const m = f.payload.match;
  if (!m) return null;
  const meta = MATCH_LABEL[m.status] ?? { label: m.status, tone: "muted" };
  return (
    <div className="match-box">
      <div className="meter">
        <span className={`chip tone-${meta.tone}`}>{meta.label}</span>
        {m.status !== "unmatched" && (
          <>
            <AsciiBar fraction={m.score / 100} width={20} />
            <span>{m.score}/100</span>
          </>
        )}
        {m.method && <span className="muted" style={{ fontSize: 12 }}>{m.method}</span>}
      </div>
      {(m.reasons.length > 0 || m.notes.length > 0) && (
        <ul className="reasons">
          {m.reasons.map(([r, p]) => (
            <li key={r}><span>{r}</span><span className="p">{p ? `+${p}` : "✓"}</span></li>
          ))}
          {m.notes.map((n) => (
            <li key={n} className="note"><span>{n}</span><span className="p">·</span></li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Candidates({ f, onConfirm, busy }: { f: FindingDetail; onConfirm: (key: string) => void; busy: boolean }) {
  const closed = f.status === "expected" || f.status === "resolved";
  return (
    <div className="cands">
      {f.payload.candidates.map((c: Candidate, i) => {
        const chosen = f.override?.target_key === c.target_key;
        return (
          <div className={`cand${chosen ? " chosen" : ""}`} key={c.target_key}>
            <div>
              <div className="head">
                <span className="mono" style={{ fontWeight: 600 }}>Candidate {String.fromCharCode(65 + i)}</span>
                {c.origin === "ai" && <span className="chip tone-ai">AI suggestion · local model</span>}
                <span className="mono muted" style={{ fontSize: 12 }}>
                  {c.record.file}:r{c.record.row} · {c.record.id}
                </span>
                {c.taken_by && <span className="chip tone-muted">already matched to {c.taken_by}</span>}
                {chosen && <span className="chip tone-teal">confirmed by {f.override?.reviewer}</span>}
              </div>
              <dl>
                {Object.entries(c.summary).map(([k, v]) => (
                  <div key={k}>
                    <dt>{k.replace(/_/g, " ")}</dt>
                    <dd>{v ?? "—"}</dd>
                  </div>
                ))}
              </dl>
              <div className="meter" style={{ marginTop: 10 }}>
                <AsciiBar fraction={c.score / 100} width={16} />
                {c.origin === "ai" ? (
                  <>
                    <span>similarity {(c.similarity ?? 0).toFixed(2)}</span>
                    <span className="muted" style={{ fontSize: 12 }}>adds no match points — you decide</span>
                  </>
                ) : (
                  <>
                    <span>{c.score}/100</span>
                    <span className="muted" style={{ fontSize: 12 }}>auto-match needs 60</span>
                  </>
                )}
              </div>
              <ul className="reasons">
                {c.reasons.map(([r, p]) => (
                  <li key={r}><span>{r}</span><span className="p">{p ? `+${p}` : "✓"}</span></li>
                ))}
                {c.notes.map((n) => (
                  <li key={n} className="note neg"><span>{n}</span><span className="p">−</span></li>
                ))}
              </ul>
            </div>
            <div>
              <button className="btn sm ink" disabled={busy || closed || !!c.taken_by || !f.active}
                      onClick={() => onConfirm(c.target_key)}>
                Confirm this match
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function RawRows({ f }: { f: FindingDetail }) {
  const recs = [f.payload.source, f.payload.target, ...(f.payload.extra_targets || [])].filter(Boolean) as EvidenceRecord[];
  if (!recs.length) return null;
  return (
    <details className="disclose">
      <summary>Original source rows ({recs.length})</summary>
      {recs.map((r) => (
        <div key={`${r.side}${r.key}`} className="raw" aria-label={`Raw row ${r.file} row ${r.row}`}>
          <span className="hl">{r.side === "before" ? "LEGACY" : "CONSOLIDATED"} · {r.file} · row {r.row}</span>
          {"\n"}
          {Object.entries(r.raw).map(([k, v]) => (
            <span key={k}>
              <span className="k">{k.padEnd(18, " ")}</span> {v === "" ? "∅" : v}
              {"\n"}
            </span>
          ))}
        </div>
      ))}
    </details>
  );
}

function useReviewPanel(f: FindingDetail) {
  const { reviewer, setReviewer } = useAppState();
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const act = useMutation({
    mutationFn: (v: { action: string; candidate?: string }) =>
      api.review(f.id, { action: v.action, reviewer: reviewer.trim(), note: note.trim() || undefined, candidate: v.candidate }),
    onSuccess: (d, v) => {
      qc.setQueryData(["finding", f.id], d);
      for (const k of ["summary", "findings", "unitmap", "reviews", "report", "deal"]) qc.invalidateQueries({ queryKey: [k, f.deal_id] });
      qc.invalidateQueries({ queryKey: ["deals"] });
      setNote("");
      setErr(null);
      toast(ACTION_DONE[v.action] ?? "Saved");
    },
    onError: (e: Error) => setErr(e.message),
  });

  const go = (action: string, candidate?: string) => {
    if (!reviewer.trim()) return setErr("Add your name in the Reviewer field so the decision is attributed.");
    if (action === "expected_exception" && note.trim().length < 3)
      return setErr("Write a short reason for the expected exception (e.g. “Tenant moved out 9/28 — lease terminated”).");
    if (action === "comment" && !note.trim()) return setErr("Write a comment first.");
    setErr(null);
    act.mutate({ action, candidate });
  };

  const s = f.status;
  const busy = act.isPending;
  return { go, busy, panel: (
    <div className="review-box">
      {!f.active && (
        <div className="banner info" style={{ marginBottom: 12 }}>
          <span className="ico">◌</span>
          <span className="grow">
            No longer detected in the latest run. History is kept.{" "}
            {f.override ? "You confirmed a match here — reopen to undo it; the next run will re-evaluate the lease."
              : "Only comments can be added."}
          </span>
        </div>
      )}
      {f.needs_rereview && (
        <div className="banner" style={{ marginBottom: 12 }}>
          <span className="ico">!</span>
          <span className="grow">The source data changed since this was reviewed. Please re-review the evidence above.</span>
        </div>
      )}
      <div className="form-grid">
        <div className="field">
          <label htmlFor="rv-name">Reviewer</label>
          <input id="rv-name" className="inp" value={reviewer} maxLength={60} onChange={(e) => setReviewer(e.target.value)} />
        </div>
        <div className="field">
          <span className="lab">Current status</span>
          <div style={{ paddingTop: 7 }}><StatusPill status={s} rereview={f.needs_rereview} /></div>
        </div>
        <div className="field full">
          <label htmlFor="rv-note">Note {s !== "expected" && <span className="muted" style={{ textTransform: "none", letterSpacing: 0 }}>— required for an expected exception</span>}</label>
          <textarea id="rv-note" className="inp" value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)}
                    placeholder="What did you check? e.g. “Confirmed with PM: tenant gave notice, moved out 9/28.”" />
        </div>
      </div>
      {err && <p className="errline" role="alert" style={{ marginTop: 10 }}>✕ {err}</p>}
      <div className="acts">
        {f.active && s === "open" && <button className="btn sm" disabled={busy} onClick={() => go("investigate")}>Investigate</button>}
        {f.active && s !== "expected" && (
          <button className="btn sm" disabled={busy} onClick={() => go("expected_exception")}>Mark expected exception</button>
        )}
        {f.active && s !== "resolved" && <button className="btn sm ink" disabled={busy} onClick={() => go("resolve")}>Resolved</button>}
        {f.active && s !== "open" && <button className="btn sm ghost" disabled={busy} onClick={() => go("reopen")}>Reopen</button>}
        {!f.active && f.override && s !== "open" && (
          <button className="btn sm ink" disabled={busy} onClick={() => go("reopen")}>Undo confirmed match (reopen)</button>
        )}
        <button className="btn sm ghost" disabled={busy} onClick={() => go("comment")}>Add comment</button>
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
        Decisions are saved with your name and a timestamp. MergeProof never edits the source exports or any vendor system.
      </p>
    </div>
  ) };
}

function Timeline({ f }: { f: FindingDetail }) {
  if (!f.events.length) return <p className="muted" style={{ fontSize: 13.5 }}>No review actions yet. Detected {when(f.created_at)}.</p>;
  const LABEL: Record<string, string> = {
    investigate: "started investigating", expected_exception: "marked as expected exception", resolve: "resolved",
    reopen: "reopened", comment: "commented", confirm_match: "confirmed a match", evidence_changed: "flagged evidence change",
  };
  return (
    <ol className="timeline">
      {f.events.map((e) => (
        <li key={e.id} className={e.action === "evidence_changed" ? "sys" : ""}>
          <span className="who">{e.reviewer}</span> {LABEL[e.action] ?? e.action}
          {e.from_status !== e.to_status && e.to_status && (
            <span className="muted"> ({e.from_status} → {e.to_status})</span>
          )}
          {e.action === "confirm_match" && e.payload?.candidate_id ? <span className="mono"> · {String(e.payload.candidate_id)}</span> : null}
          <div className="when">{when(e.created_at)}</div>
          {e.note && <div className="note">{e.note}</div>}
        </li>
      ))}
    </ol>
  );
}

export function EvidenceDrawer({ findingId, onClose, onOpen }: {
  findingId: string; onClose: () => void; onOpen: (id: string) => void;
}) {
  const q = useQuery({ queryKey: ["finding", findingId], queryFn: () => api.finding(findingId) });
  return (
    <Drawer label="Finding evidence" onClose={onClose}>
      {q.isLoading && (
        <>
          <div className="drawer-head" style={{ minHeight: 120 }}>
            <button className="drawer-close" onClick={onClose} aria-label="Close evidence"><X size={18} /></button>
          </div>
          <Loading label="Loading evidence" />
        </>
      )}
      {q.error && (
        <div className="drawer-body">
          <button className="btn sm" onClick={onClose}>Close</button>
          <div style={{ marginTop: 12 }}><ErrorBox error={q.error} onRetry={() => q.refetch()} /></div>
        </div>
      )}
      {q.data && <DrawerContent f={q.data} onClose={onClose} onOpen={onOpen} />}
    </Drawer>
  );
}

function DrawerContent({ f, onClose, onOpen }: { f: FindingDetail; onClose: () => void; onOpen: (id: string) => void }) {
  const review = useReviewPanel(f);
  const p = f.payload;
  return (
    <>
      <div className="drawer-head on-ink">
        <button className="drawer-close" onClick={onClose} aria-label="Close evidence" data-autofocus><X size={18} /></button>
        <div className="top">
          <SevChip severity={f.severity} />
          <span>{f.rule_id}</span>
          <span>·</span>
          <span>{ENTITY_LABEL[f.entity] ?? f.entity}</span>
          <span>·</span>
          <span>{f.id}</span>
        </div>
        <h2>{f.title}</h2>
        <div className="subj">{f.subject}</div>
        <div className={`stamp ${f.status}`} aria-hidden="true">{f.status === "expected" ? "Expected" : STATUS_LABEL[f.status]}</div>
      </div>
      <div className="drawer-body">
        <p className="explain">{p.explanation}</p>

        {p.impact?.calc && (
          <div className="drawer-section">
            <Kicker sec="A">The money</Kicker>
            <Receipt f={f} />
          </div>
        )}

        {p.compare.length > 0 && (
          <div className="drawer-section">
            <Kicker sec="B">Evidence · before and after</Kicker>
            <Compare f={f} />
            <p className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>
              Rows show the original cell value; a second line shows the normalized value used for comparison.
              <b> differs</b> = a real change · <b>format only</b> = same value, different formatting ·
              <b> re-keyed</b> = new system ID (expected, not an anomaly).
            </p>
          </div>
        )}

        {p.extra_targets?.length > 0 && (
          <div className="drawer-section">
            <Kicker sec="B·2">Additional consolidated records</Kicker>
            <div className="stack tight">
              {[p.target, ...p.extra_targets].filter(Boolean).map((r) => (
                <div className="match-box mono" key={r!.key} style={{ fontSize: 13 }}>
                  {r!.id} · {r!.file}:r{r!.row}
                  {Object.entries(r!.context || {}).map(([k, v]) => (
                    <span className="muted" key={k}> · {k}: {String(v)}</span>
                  ))}
                  <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                    {Object.entries(r!.raw).map(([k, v]) => `${k}: ${v}`).join("  ·  ")}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {p.members?.length > 0 && (
          <div className="drawer-section">
            <Kicker sec="B·2">Rows covered ({p.members.length})</Kicker>
            <div className="table-wrap">
              <table className="t compact">
                <thead><tr>{Object.keys(p.members[0]).map((k) => <th key={k}>{k}</th>)}</tr></thead>
                <tbody>
                  {p.members.map((m, i) => (
                    <tr key={i}>{Object.values(m).map((v, j) => <td key={j} className="mono" style={{ fontSize: 12.5 }}>{v ?? "—"}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {p.candidates?.length > 0 && (
          <div className="drawer-section">
            <Kicker sec="C">Possible matches — your call</Kicker>
            <p className="muted" style={{ fontSize: 13.5, marginBottom: 10 }}>
              {p.candidates.some((c) => c.origin === "ai")
                ? "The rules found no candidate. The local AI model, running on this server, suggests the record below because its description reads similarly. It has not been matched and changes no totals. Confirm it only if it's the same request."
                : "No candidate is strong enough to accept automatically. Confirm one, or leave this open and investigate."}{" "}
              A confirmed match is applied to downstream checks on the next audit run.
            </p>
            <Candidates f={f} busy={review.busy} onConfirm={(k) => review.go("confirm_match", k)} />
          </div>
        )}

        {p.match && (
          <div className="drawer-section">
            <Kicker sec={p.candidates?.length ? "D" : "C"}>How the records were paired</Kicker>
            <MatchBox f={f} />
          </div>
        )}

        <div className="drawer-section">
          <Kicker sec="→">Recommended next action</Kicker>
          <p style={{ fontSize: 15 }}>{p.recommended_action}</p>
          {f.rule && <p className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>Check {f.rule_id}: {f.rule.check}</p>}
        </div>

        <div className="drawer-section">
          <RawRows f={f} />
        </div>

        {f.related.length > 0 && (
          <div className="drawer-section">
            <Kicker sec="+">Other findings on this lease</Kicker>
            <div className="stack tight">
              {f.related.map((r) => (
                <button key={r.id} className="attn-item" style={{ gridTemplateColumns: "auto 1fr auto", padding: "10px 12px",
                        border: "1px solid var(--rule)", borderRadius: 6, background: "var(--paper-2)" }} onClick={() => onOpen(r.id)}>
                  <SevChip severity={r.severity} />
                  <span><b>{r.title}</b> <span className="mono muted" style={{ fontSize: 12 }}>{r.rule_id} · {r.id}</span></span>
                  <StatusPill status={r.status} />
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="drawer-section">
          <Kicker sec="✎">Review</Kicker>
          {review.panel}
        </div>

        <div className="drawer-section">
          <Kicker sec="⏱">Review log</Kicker>
          <Timeline f={f} />
        </div>
      </div>
    </>
  );
}
