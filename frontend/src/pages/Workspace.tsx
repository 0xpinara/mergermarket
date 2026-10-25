import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, Download, Pencil, Trash2 } from "lucide-react";
import { api, ApiError, sampleFileUrl, type Deal, type SchemaInfo, type Side, type Snapshot } from "../lib/api";
import { useAppState } from "../lib/store";
import { day, num } from "../lib/format";
import { DealStateChip, ErrorBox, Kicker, Loading, Modal, useToast } from "../components/ui";
import { RunAudit } from "../components/RunAudit";
import { DealForm } from "../components/DealForm";

export function useRouteDeal() {
  const { id } = useParams();
  const { dealId, setDealId } = useAppState();
  useEffect(() => {
    if (id && id !== dealId) setDealId(id);
  }, [id, dealId, setDealId]);
  return id!;
}

function invalidateDeal(qc: ReturnType<typeof useQueryClient>, id: string) {
  for (const k of ["deal", "summary"]) qc.invalidateQueries({ queryKey: [k, id] });
  qc.invalidateQueries({ queryKey: ["deals"] });
}

function SnapshotCard({ snap, schema, dealId }: { snap: Snapshot; schema: SchemaInfo; dealId: string }) {
  const needs = snap.validation.status !== "ready";
  const [open, setOpen] = useState(needs);
  const [entity, setEntity] = useState(snap.entity);
  const [mapping, setMapping] = useState<Record<string, string>>(snap.mapping);
  const [meta, setMeta] = useState(snap.mapping_meta);
  const [err, setErr] = useState<string | null>(null);
  const [confirmDel, setConfirmDel] = useState(false);
  const qc = useQueryClient();
  const toast = useToast();

  useEffect(() => {
    setEntity(snap.entity);
    setMapping(snap.mapping);
    setMeta(snap.mapping_meta);
  }, [snap.id, snap.updated_at, snap.entity, snap.mapping, snap.mapping_meta]);

  const spec = schema.entities.find((e) => e.key === entity)!;
  const dirty = entity !== snap.entity || JSON.stringify(mapping) !== JSON.stringify(snap.mapping);
  const used = new Set(Object.values(mapping));
  const sampleRow = snap.preview[0]?.values ?? [];

  const save = useMutation({
    mutationFn: () => api.updateMapping(snap.id, entity, mapping),
    onSuccess: (s) => {
      invalidateDeal(qc, dealId);
      setErr(null);
      toast(s.validation.status === "ready" ? `Mapping saved — ${s.file_name} is ready` : "Mapping saved — still missing fields");
    },
    onError: (e: Error) => setErr(e.message),
  });
  const del = useMutation({
    mutationFn: () => api.deleteSnapshot(snap.id),
    onSuccess: () => {
      invalidateDeal(qc, dealId);
      toast(`Removed ${snap.file_name}`);
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  const changeEntity = async (next: string) => {
    setEntity(next);
    try {
      const r = await api.suggest(snap.id, next);
      setMapping(r.mapping);
      setMeta(r.meta);
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  const missing = spec.fields.filter((f) => f.required && !mapping[f.key]).map((f) => f.label);
  for (const g of spec.one_of) if (!g.some((k) => mapping[k])) missing.push(g.map((k) => spec.fields.find((f) => f.key === k)?.label).join(" or "));
  const ext = snap.file_name.toLowerCase().endsWith(".xlsx") ? "XLSX" : "CSV";
  const name = snap.sheet ? `${snap.file_name} › ${snap.sheet}` : snap.file_name;
  const ready = !dirty ? snap.validation.status === "ready" : missing.length === 0;

  return (
    <div className="snap">
      <div className="snap-head">
        <span className="ftype" aria-hidden="true">{ext}</span>
        <div style={{ minWidth: 0 }}>
          <div className="fname" title={name}>{name}</div>
          <div className="meta">
            <span className="mono">{num(snap.row_count)} rows</span>
            <span>·</span>
            <span>{spec.label}</span>
            {snap.origin === "seed" && <span className="chip tone-muted">sample</span>}
            {Object.keys(snap.ai_suggestions ?? {}).length > 0 && (
              <span className="chip tone-ai" title="Column suggestions from the local AI model, waiting for your review">
                AI · {Object.keys(snap.ai_suggestions).length} suggestion{Object.keys(snap.ai_suggestions).length === 1 ? "" : "s"}
              </span>
            )}
            {ready ? <span className="chip tone-lime">✓ ready</span> : <span className="chip tone-amber">needs mapping</span>}
          </div>
        </div>
        <div className="row" style={{ gap: 6 }}>
          <button className="btn sm ghost" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            {open ? <ChevronUp /> : <ChevronDown />} Mapping
          </button>
          <button className="icon-btn" onClick={() => setConfirmDel(true)} aria-label={`Remove ${name}`}>
            <Trash2 />
          </button>
        </div>
      </div>
      {open && (
        <div className="snap-body">
          <div className="row" style={{ marginBottom: 10 }}>
            <label className="mono muted" style={{ fontSize: 11, letterSpacing: ".08em", textTransform: "uppercase" }} htmlFor={`ent-${snap.id}`}>
              Record type
            </label>
            <select id={`ent-${snap.id}`} className="sel" value={entity} onChange={(e) => changeEntity(e.target.value)}>
              {schema.entities.map((e) => <option key={e.key} value={e.key}>{e.label}</option>)}
            </select>
            {snap.detected_entity === entity && <span className="conf hi">auto-detected</span>}
          </div>
          <div className="table-wrap">
            <table className="map-table">
              <thead>
                <tr>
                  <th>Canonical field</th>
                  <th>Column in your file</th>
                  <th>Match</th>
                  <th>First value</th>
                </tr>
              </thead>
              <tbody>
                {spec.fields.map((f) => {
                  const col = mapping[f.key] ?? "";
                  const m = meta[f.key];
                  const ai = snap.ai_suggestions?.[f.key];
                  const aiApplied = !!(ai && col && ai.column === col && (!m || m.column !== col));
                  const user = col && (!m || m.column !== col) && !aiApplied;
                  const idx = snap.headers.indexOf(col);
                  const aiOpen = ai && !col && !used.has(ai.column);
                  return (
                    <tr key={f.key}>
                      <td>
                        {f.label} {f.required && <span className="req" title="Required">*</span>}
                      </td>
                      <td>
                        <select className="sel" aria-label={`Column for ${f.label}`} value={col}
                                onChange={(e) => setMapping((mp) => {
                                  const n = { ...mp };
                                  if (e.target.value) n[f.key] = e.target.value;
                                  else delete n[f.key];
                                  return n;
                                })}>
                          <option value="">— not mapped —</option>
                          {snap.headers.map((h) => (
                            <option key={h} value={h} disabled={used.has(h) && h !== col}>{h}</option>
                          ))}
                        </select>
                        {aiOpen && (
                          <div className="ai-hint">
                            <span className="ai-badge" title="Suggested by the local AI model — not applied until you click Apply">AI</span>
                            <span>suggests <b>{ai!.column}</b> · {Math.round(ai!.similarity * 100)}% similar</span>
                            <button type="button" className="link-btn"
                                    onClick={() => setMapping((mp) => ({ ...mp, [f.key]: ai!.column }))}>Apply</button>
                          </div>
                        )}
                      </td>
                      <td>
                        {aiApplied ? <span className="conf user">AI · applied by you</span> : user ? <span className="conf user">you</span> : m ? (
                          <span className={`conf ${m.confidence >= 0.85 ? "hi" : "mid"}`} title={m.method}>
                            {Math.round(m.confidence * 100)}% {m.confidence >= 1 ? "exact" : m.method.split(" ")[0]}
                          </span>
                        ) : <span className="conf muted">—</span>}
                      </td>
                      <td className="sample" title={idx >= 0 ? sampleRow[idx] : ""}>{idx >= 0 ? sampleRow[idx] || "∅" : ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {missing.length > 0 && <p className="errline" style={{ marginTop: 10 }}>✕ Map: {missing.join(", ")}</p>}
          {!dirty && snap.validation.warnings.map((w) => <p key={w} className="warnline" style={{ marginTop: 6 }}>! {w}</p>)}
          {snap.warnings.map((w) => <p key={w} className="warnline" style={{ marginTop: 6 }}>! {w}</p>)}
          {err && <p className="errline" role="alert" style={{ marginTop: 8 }}>✕ {err}</p>}
          <div className="btn-row" style={{ marginTop: 12 }}>
            <button className="btn sm ink" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
              {save.isPending ? "Saving…" : "Save mapping"}
            </button>
            {dirty && (
              <button className="btn sm ghost" onClick={() => { setEntity(snap.entity); setMapping(snap.mapping); setMeta(snap.mapping_meta); }}>
                Discard changes
              </button>
            )}
            <span className="muted" style={{ fontSize: 12 }}>Changing a mapping marks the audit for rerun.</span>
          </div>
          <details className="disclose" style={{ marginTop: 14 }}>
            <summary>Preview first {snap.preview.length} rows</summary>
            <div className="table-wrap" style={{ marginTop: 8 }}>
              <table className="t compact preview">
                <thead>
                  <tr>
                    <th>row</th>
                    {snap.headers.map((h) => <th key={h} className={used.has(h) ? "mapped" : ""}>{h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {snap.preview.map((r) => (
                    <tr key={r.row}>
                      <td className="rn">{r.row}</td>
                      {r.values.map((v, i) => <td key={i} title={v}>{v}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </div>
      )}
      {confirmDel && (
        <Modal title="Remove this file?" kicker="Snapshot" onClose={() => setConfirmDel(false)}
               footer={<>
                 <button className="btn ghost" onClick={() => setConfirmDel(false)}>Keep it</button>
                 <button className="btn danger" onClick={() => { setConfirmDel(false); del.mutate(); }}>Remove file</button>
               </>}>
          <p><b>{name}</b> and its {num(snap.row_count)} parsed rows will be removed from this audit. Existing findings stay
            until you rerun.</p>
        </Modal>
      )}
    </div>
  );
}

function DropZone({ side, dealId, enabled }: { side: Side; dealId: string; enabled: boolean }) {
  const [over, setOver] = useState(false);
  const [errors, setErrors] = useState<{ file: string; message: string }[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const up = useMutation({
    mutationFn: (files: File[]) => api.upload(dealId, side, files),
    onSuccess: (r) => {
      setErrors(r.errors);
      invalidateDeal(qc, dealId);
      toast(`Added ${r.created.length} file${r.created.length === 1 ? "" : "s"}${r.errors.length ? ` · ${r.errors.length} rejected` : ""}`);
    },
    onError: (e: ApiError) => {
      setErrors(e.fileErrors?.length ? e.fileErrors : [{ file: "", message: e.message }]);
    },
  });
  const send = (list: FileList | null) => {
    if (!list || !list.length || !enabled) return;
    setErrors([]);
    up.mutate(Array.from(list));
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    send(e.dataTransfer.files);
  };
  return (
    <div>
      <div
        className={`drop${over ? " over" : ""}${enabled ? "" : " disabled"}`}
        role="button"
        tabIndex={enabled ? 0 : -1}
        aria-disabled={!enabled}
        aria-label={`Upload ${side === "before" ? "legacy" : "consolidated"} export files`}
        onClick={() => enabled && input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && enabled && (e.preventDefault(), input.current?.click())}
        onDragOver={(e) => { e.preventDefault(); if (enabled) setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
      >
        <span className="ascii" aria-hidden="true">{" ┌─────┐\n │ csv │──▶ ▤\n └─────┘"}</span>
        <div className="t">{up.isPending ? "Parsing…" : `Drop ${side === "before" ? "legacy" : "consolidated"} exports here`}</div>
        <div className="d">
          {enabled ? "or click to browse · CSV or XLSX · multiple files" : "Confirm the data notice above to enable uploads"}
        </div>
        <input ref={input} type="file" multiple accept=".csv,.tsv,.txt,.xlsx" hidden onChange={(e) => { send(e.target.files); e.target.value = ""; }} />
      </div>
      {errors.map((e, i) => (
        <p key={i} className="errline" role="alert" style={{ marginTop: 8 }}>✕ {e.message}</p>
      ))}
    </div>
  );
}

function SampleFiles() {
  const q = useQuery({ queryKey: ["sampleFiles"], queryFn: api.sampleFiles });
  return (
    <details className="disclose">
      <summary>Download the synthetic sample exports (to test uploads)</summary>
      <div className="grid cols-3" style={{ marginTop: 10 }}>
        {(["before", "after", "messy"] as const).map((side) => (
          <div key={side}>
            <div className="mono muted" style={{ fontSize: 11, marginBottom: 6 }}>
              {side === "before" ? "LEGACY (BEFORE)" : side === "after" ? "CONSOLIDATED (AFTER)" : "MESSY HEADERS · TRY AI"}
            </div>
            {q.data?.filter((f) => f.side === side).map((f) => (
              <a key={f.name} href={sampleFileUrl(side, f.name)} download className="row mono" style={{ fontSize: 12.5, padding: "3px 0", gap: 8 }}>
                <Download size={13} /> {f.name} <span className="muted">· {num(f.rows)} rows</span>
              </a>
            ))}
          </div>
        ))}
      </div>
    </details>
  );
}

export function Workspace() {
  const id = useRouteDeal();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const { setDealId } = useAppState();
  const dealQ = useQuery({ queryKey: ["deal", id], queryFn: () => api.deal(id) });
  const schemaQ = useQuery({ queryKey: ["schema"], queryFn: api.schema, staleTime: Infinity });
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [consent, setConsent] = useState(() => {
    try { return sessionStorage.getItem("mp.consent") === "1"; } catch { return false; }
  });
  const del = useMutation({
    mutationFn: () => api.deleteDeal(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["deals"] });
      setDealId(null);
      toast("Audit deleted");
      nav("/");
    },
  });
  const deal: Deal | undefined = dealQ.data;
  const bySide = useMemo(() => ({
    before: deal?.snapshots?.filter((s) => s.side === "before") ?? [],
    after: deal?.snapshots?.filter((s) => s.side === "after") ?? [],
  }), [deal]);

  if (dealQ.isLoading || schemaQ.isLoading) return <div className="page"><Loading /></div>;
  if (dealQ.error) return <div className="page"><ErrorBox error={dealQ.error} onRetry={() => dealQ.refetch()} /></div>;
  if (!deal || !schemaQ.data) return null;
  const schema = schemaQ.data;
  const lim = schema.limits;

  return (
    <div className="page">
      <div className="page-head">
        <div style={{ minWidth: 0 }}>
          <Kicker sec="02">Audit workspace</Kicker>
          <h1 className="page-title">{deal.title}</h1>
          <div className="row" style={{ marginTop: 10 }}>
            <DealStateChip state={deal.status.state} />
            <span className="muted" style={{ fontSize: 13.5 }}>
              {deal.acquirer && deal.target_business ? `${deal.acquirer} ← ${deal.target_business}` : deal.industry}
              {deal.deal_date && ` · cutover ${day(deal.deal_date)}`}
            </span>
          </div>
        </div>
        <div className="btn-row">
          <button className="btn ghost sm" onClick={() => setEditing(true)}><Pencil /> Edit details</button>
          <button className="btn danger sm" onClick={() => setDeleting(true)}><Trash2 /> Delete audit</button>
        </div>
      </div>

      {deal.is_sample && (
        <div className="banner info">
          <span className="ico">◇</span>
          <span className="grow">
            <b>Synthetic sample loaded.</b> {deal.source_notes} Twelve exports are already in place below — run the audit
            when you’re ready.
          </span>
        </div>
      )}

      <div className="grid split">
        <RunAudit deal={deal} />
        <div className="card pad">
          <Kicker sec="i">What goes where</Kicker>
          <p style={{ fontSize: 14 }}>
            <b>Before</b> = what {deal.target_business || "the acquired business"} had in{" "}
            <i>{deal.old_system || "the old system"}</i>. <b>After</b> = what landed in{" "}
            <i>{deal.new_system || "the consolidated system"}</i>. Use the same record types on both sides: properties,
            units, tenants, leases, security deposits, maintenance requests. Only leases are required.
          </p>
          <hr className="dash" />
          <p className="muted" style={{ fontSize: 12.5 }}>
            {lim.file_types.join(" ")} · up to {lim.max_upload_mb} MB and {num(lim.max_rows_per_file)} rows per file ·{" "}
            {lim.max_files_per_request} files per upload. Column names are auto-mapped (e.g. <span className="mono">base_rent</span>,{" "}
            <span className="mono">rent_amount</span>, <span className="mono">apartment</span>) — review and correct below.
          </p>
          <p className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>
            Retention: parsed rows are stored for evidence; the original file is not kept. Delete the audit to remove
            everything. No direct AppFolio, Buildium or Yardi connection — this audits exports you provide.
          </p>
          <div style={{ marginTop: 10 }}><SampleFiles /></div>
        </div>
      </div>

      {deal.status.problems.length > 0 && deal.status.state !== "draft" && (
        <div className="banner section">
          <span className="ico">!</span>
          <span className="grow">
            <b>Before the audit can run:</b> {deal.status.problems.join(" · ")}
          </span>
        </div>
      )}
      {deal.status.skipped.length > 0 && (
        <div className="banner info section">
          <span className="ico">i</span>
          <span className="grow">{deal.status.skipped.join(" ")}</span>
        </div>
      )}

      <div className="section">
        <label className="check">
          <input type="checkbox" checked={consent} onChange={(e) => {
            setConsent(e.target.checked);
            try { sessionStorage.setItem("mp.consent", e.target.checked ? "1" : "0"); } catch { /* ignore */ }
          }} />
          <span>
            I confirm any files I upload contain only <b>synthetic or redacted</b> data. This is a public demo environment
            without tenant separation or a security review — never upload real tenant or bank details.
          </span>
        </label>
      </div>

      <div className="sides section">
        {(["before", "after"] as const).map((side) => (
          <section className="side-col" key={side} aria-labelledby={`side-${side}`}>
            <div className="side-head">
              <h3 id={`side-${side}`}>
                <span className="tag">{side === "before" ? "BEFORE" : "AFTER"}</span>
                {side === "before" ? "Legacy export" : "Consolidated import"}
              </h3>
              <span className="sys">
                {side === "before" ? deal.old_system || "old system" : deal.new_system || "new system"} ·{" "}
                {num(bySide[side].reduce((a, s) => a + s.row_count, 0))} rows
              </span>
            </div>
            <DropZone side={side} dealId={deal.id} enabled={consent} />
            {bySide[side].length === 0 && (
              <p className="muted" style={{ fontSize: 13.5, textAlign: "center" }}>No {side === "before" ? "legacy" : "consolidated"} files yet.</p>
            )}
            {bySide[side].map((s) => <SnapshotCard key={s.id} snap={s} schema={schema} dealId={deal.id} />)}
          </section>
        ))}
      </div>

      {editing && <DealForm deal={deal} onClose={() => setEditing(false)} />}
      {deleting && (
        <Modal title="Delete this audit?" kicker="Irreversible" onClose={() => setDeleting(false)}
               footer={<>
                 <button className="btn ghost" onClick={() => setDeleting(false)}>Cancel</button>
                 <button className="btn danger" onClick={() => del.mutate()} disabled={del.isPending}>Delete audit and data</button>
               </>}>
          <p>This removes <b>{deal.title}</b>: all uploaded rows, audit runs, findings and the review log.</p>
          {del.error && <p className="errline" style={{ marginTop: 10 }}>✕ {(del.error as Error).message}</p>}
        </Modal>
      )}
    </div>
  );
}
