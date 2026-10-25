import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Pencil, Plus, Trash2 } from "lucide-react";
import { api, type IntelNote } from "../lib/api";
import { useAppState } from "../lib/store";
import { day } from "../lib/format";
import { Empty, ErrorBox, Kicker, Loading, Modal, useToast } from "../components/ui";

const PILOT: Record<string, { label: string; tone: string }> = {
  researching: { label: "Researching", tone: "muted" },
  contacted: { label: "Contacted", tone: "amber" },
  interviewing: { label: "Interviewing", tone: "amber" },
  pilot_proposed: { label: "Pilot proposed", tone: "teal" },
  pilot_active: { label: "Pilot active", tone: "lime" },
  not_a_fit: { label: "Not a fit", tone: "coral" },
};

const BLANK = { company_name: "", sector: "Property management", location: "", event_date: "", website: "", source_link: "",
                notes: "", contact_role: "", pilot_status: "researching", linked_deal_id: "" };
type F = typeof BLANK;

function validUrl(v: string) {
  return !v || /^https?:\/\/[^\s<>"']+$/i.test(v.trim());
}

function IntelForm({ note, onClose }: { note?: IntelNote; onClose: () => void }) {
  const { deals } = useAppState();
  const [f, setF] = useState<F>(() => note
    ? (Object.fromEntries(Object.keys(BLANK).map((k) => [k, (note[k as keyof IntelNote] as string | null) ?? ""])) as F)
    : BLANK);
  const [errors, setErrors] = useState<Partial<Record<keyof F, string>>>({});
  const [serverErr, setServerErr] = useState<string | null>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const save = useMutation({
    mutationFn: () => {
      const body = Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v.trim() || null])) as Partial<IntelNote>;
      body.pilot_status = f.pilot_status;
      return note ? api.updateIntel(note.id, body) : api.createIntel(body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["intel"] });
      toast(note ? "Prospect updated" : "Prospective integration added");
      onClose();
    },
    onError: (e: Error) => setServerErr(e.message),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const errs: typeof errors = {};
    if (!f.company_name.trim()) errs.company_name = "Company name is required.";
    if (!validUrl(f.website)) errs.website = "Use a full http(s):// link.";
    if (!validUrl(f.source_link)) errs.source_link = "Use a full http(s):// link.";
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setServerErr(null);
    save.mutate();
  };
  const bind = (k: keyof F) => ({
    id: `intel-${k}`, value: f[k], onChange: (e: { target: { value: string } }) => setF((s) => ({ ...s, [k]: e.target.value })),
    "aria-invalid": !!errors[k], "aria-describedby": errors[k] ? `intel-${k}-err` : undefined,
  });
  const err = (k: keyof F) => errors[k] && <span className="err" id={`intel-${k}-err`}>{errors[k]}</span>;
  return (
    <Modal title={note ? "Edit prospect" : "Add prospective integration"} kicker="Deal intelligence · manual entry"
           onClose={onClose}
           footer={<>
             <button className="btn ghost" onClick={onClose}>Cancel</button>
             <button className="btn primary" type="submit" form="intel-form" disabled={save.isPending}>
               {save.isPending ? "Saving…" : "Save prospect"}
             </button>
           </>}>
      <form id="intel-form" onSubmit={submit} noValidate>
        <div className="form-grid">
          <div className="field full">
            <label htmlFor="intel-company_name">Company *</label>
            <input className="inp" maxLength={160} {...bind("company_name")} data-autofocus placeholder="Acquired or acquiring property manager" />
            {err("company_name")}
          </div>
          <div className="field"><label htmlFor="intel-sector">Sector</label><input className="inp" maxLength={120} {...bind("sector")} /></div>
          <div className="field"><label htmlFor="intel-location">Location</label><input className="inp" maxLength={120} {...bind("location")} placeholder="City, state" /></div>
          <div className="field"><label htmlFor="intel-event_date">Acquisition / event date</label><input className="inp" type="date" {...bind("event_date")} /></div>
          <div className="field"><label htmlFor="intel-contact_role">Buyer / contact role</label><input className="inp" maxLength={120} {...bind("contact_role")} placeholder="Owner, COO, integration lead" /></div>
          <div className="field"><label htmlFor="intel-website">Website</label><input className="inp" type="url" {...bind("website")} placeholder="https://" />{err("website")}</div>
          <div className="field"><label htmlFor="intel-source_link">Public source link</label><input className="inp" type="url" {...bind("source_link")} placeholder="Press release or announcement" />{err("source_link")}</div>
          <div className="field">
            <label htmlFor="intel-pilot_status">Pilot status</label>
            <select className="sel" {...bind("pilot_status")}>
              {Object.entries(PILOT).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="intel-linked_deal_id">Linked audit</label>
            <select className="sel" {...bind("linked_deal_id")}>
              <option value="">— none —</option>
              {deals.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}
            </select>
          </div>
          <div className="field full">
            <label htmlFor="intel-notes">Your research notes</label>
            <textarea className="inp" maxLength={4000} {...bind("notes")}
                      placeholder="Your own observations. Don't paste licensed database content." />
            <span className="hint">Stored in this demo workspace only. Never enter passwords or paste PitchBook exports.</span>
          </div>
        </div>
        {serverErr && <p className="errline" role="alert" style={{ marginTop: 12 }}>✕ {serverErr}</p>}
      </form>
    </Modal>
  );
}

export function Intel() {
  const q = useQuery({ queryKey: ["intel"], queryFn: api.intel });
  const { deals } = useAppState();
  const [editing, setEditing] = useState<IntelNote | "new" | null>(null);
  const [deleting, setDeleting] = useState<IntelNote | null>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const del = useMutation({
    mutationFn: (id: string) => api.deleteIntel(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["intel"] }); toast("Prospect removed"); setDeleting(null); },
    onError: (e: Error) => toast(e.message, "err"),
  });
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <Kicker sec="05">Deal intelligence</Kicker>
          <h1 className="page-title">Who just <em>bought</em> whom?</h1>
          <p className="page-sub">
            A lightweight list of prospective integrations — property managers that recently acquired a portfolio or
            switched software — so the next continuity audit is a warm conversation, not a cold one.
          </p>
        </div>
        <div className="btn-row">
          <a className="btn ghost" href="https://pitchbook.com/" target="_blank" rel="noopener noreferrer">
            Research in PitchBook <ExternalLink />
          </a>
          <button className="btn primary" onClick={() => setEditing("new")}><Plus /> Add prospective integration</button>
        </div>
      </div>
      <div className="notice">
        <span className="g">!</span>
        <span>
          <b>Direct PitchBook data integration requires a separate license and is not active in this demo.</b> The link
          opens PitchBook’s public site in a new tab for users with their own authorized access. MergeProof does not
          connect to, scrape or import PitchBook data, and never asks for PitchBook credentials. Record only your own,
          non-proprietary notes here.
        </span>
      </div>
      <div className="section">
        {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} onRetry={() => q.refetch()} /> : q.data!.length === 0 ? (
          <Empty art={" ┌─┐  ┌─┐\n └┬┘──└┬┘\n  └─▶──┘"} title="No prospects yet"
                 actions={<button className="btn primary" onClick={() => setEditing("new")}><Plus /> Add the first one</button>}>
            Track acquisitions you hear about from public announcements or your own research.
          </Empty>
        ) : (
          <div className="grid cols-3">
            {q.data!.map((n) => {
              const st = PILOT[n.pilot_status] ?? PILOT.researching;
              const linked = deals.find((d) => d.id === n.linked_deal_id);
              return (
                <article key={n.id} className="intel-card">
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <span className={`chip tone-${st.tone}`}>{st.label}</span>
                    <span className="row" style={{ gap: 6 }}>
                      <button className="icon-btn" aria-label={`Edit ${n.company_name}`} onClick={() => setEditing(n)}><Pencil /></button>
                      <button className="icon-btn" aria-label={`Delete ${n.company_name}`} onClick={() => setDeleting(n)}><Trash2 /></button>
                    </span>
                  </div>
                  <h4>{n.company_name}</h4>
                  <div className="meta">
                    {n.sector && <span>{n.sector}</span>}
                    {n.location && <span>· {n.location}</span>}
                    {n.event_date && <span>· event {day(n.event_date)}</span>}
                    {n.contact_role && <span>· {n.contact_role}</span>}
                  </div>
                  {n.notes && <p className="notes">{n.notes}</p>}
                  <div className="row mono" style={{ fontSize: 12, gap: 12 }}>
                    {n.website && <a href={n.website} target="_blank" rel="noopener noreferrer nofollow">website ↗</a>}
                    {n.source_link && <a href={n.source_link} target="_blank" rel="noopener noreferrer nofollow">source ↗</a>}
                    {linked && <span className="muted">audit: {linked.title}</span>}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>
      {editing && <IntelForm note={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} />}
      {deleting && (
        <Modal title="Remove prospect?" onClose={() => setDeleting(null)}
               footer={<>
                 <button className="btn ghost" onClick={() => setDeleting(null)}>Cancel</button>
                 <button className="btn danger" onClick={() => del.mutate(deleting.id)} disabled={del.isPending}>Remove</button>
               </>}>
          <p>Remove <b>{deleting.company_name}</b> and its notes?</p>
        </Modal>
      )}
    </div>
  );
}
