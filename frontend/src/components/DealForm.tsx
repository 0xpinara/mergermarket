import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, type Deal } from "../lib/api";
import { useAppState } from "../lib/store";
import { Modal, useToast } from "./ui";

const EMPTY = {
  title: "", acquirer: "", target_business: "", industry: "Property management", deal_date: "",
  old_system: "", new_system: "", source_notes: "", source_ref: "",
};

type FormState = typeof EMPTY;

export function DealForm({ deal, onClose }: { deal?: Deal; onClose: () => void }) {
  const [f, setF] = useState<FormState>(() =>
    deal
      ? (Object.fromEntries(Object.keys(EMPTY).map((k) => [k, (deal[k as keyof Deal] as string | null) ?? ""])) as FormState)
      : EMPTY,
  );
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  const nav = useNavigate();
  const toast = useToast();
  const { setDealId } = useAppState();

  const save = useMutation({
    mutationFn: () => {
      const body = Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v.trim() || null])) as Partial<Deal>;
      return deal ? api.updateDeal(deal.id, body) : api.createDeal(body);
    },
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["deals"] });
      qc.invalidateQueries({ queryKey: ["deal", d.id] });
      qc.invalidateQueries({ queryKey: ["summary", d.id] });
      toast(deal ? "Audit details saved" : "Audit workspace created");
      if (!deal) {
        setDealId(d.id);
        nav(`/deals/${d.id}/workspace`);
      }
      onClose();
    },
    onError: (e: Error) => setError(e.message),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!f.title.trim()) {
      setError("Give the audit a name, e.g. “Cedar × Harborline | Rent roll migration”.");
      return;
    }
    setError(null);
    save.mutate();
  };

  const bind = (k: keyof FormState) => ({
    value: f[k],
    onChange: (e: { target: { value: string } }) => setF((s) => ({ ...s, [k]: e.target.value })),
    id: `deal-${k}`,
    name: k,
  });

  return (
    <Modal
      title={deal ? "Edit audit details" : "Start an audit"}
      kicker={deal ? "Deal / workspace" : "New integration audit"}
      onClose={onClose}
      footer={
        <>
          <button className="btn ghost" type="button" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" type="submit" form="deal-form" disabled={save.isPending}>
            {save.isPending ? "Saving…" : deal ? "Save details" : "Create workspace"}
          </button>
        </>
      }
    >
      <form id="deal-form" onSubmit={submit} noValidate>
        <div className="form-grid">
          <div className="field full">
            <label htmlFor="deal-title">Audit name *</label>
            <input className="inp" {...bind("title")} maxLength={160} data-autofocus
                   placeholder="Cedar × Harborline | Tenant & rent roll migration" aria-invalid={!!error && !f.title.trim()} />
          </div>
          <div className="field">
            <label htmlFor="deal-acquirer">Acquirer (your firm)</label>
            <input className="inp" {...bind("acquirer")} maxLength={120} placeholder="Cedar Property Management" />
          </div>
          <div className="field">
            <label htmlFor="deal-target_business">Business being integrated</label>
            <input className="inp" {...bind("target_business")} maxLength={120} placeholder="Harborline Rentals" />
          </div>
          <div className="field">
            <label htmlFor="deal-old_system">Old system nickname</label>
            <input className="inp" {...bind("old_system")} maxLength={120} placeholder="Legacy PMS" />
          </div>
          <div className="field">
            <label htmlFor="deal-new_system">New system nickname</label>
            <input className="inp" {...bind("new_system")} maxLength={120} placeholder="Consolidated platform" />
          </div>
          <div className="field">
            <label htmlFor="deal-deal_date">Cutover / as-of date</label>
            <input className="inp" type="date" {...bind("deal_date")} />
            <span className="hint">Used to judge whether a lease is current.</span>
          </div>
          <div className="field">
            <label htmlFor="deal-industry">Industry</label>
            <input className="inp" {...bind("industry")} maxLength={120} />
          </div>
          <div className="field full">
            <label htmlFor="deal-source_notes">Source notes</label>
            <textarea className="inp" {...bind("source_notes")} maxLength={2000}
                      placeholder="Where the exports came from, when they were pulled, known caveats." />
          </div>
          <div className="field full">
            <label htmlFor="deal-source_ref">Source reference</label>
            <input className="inp" {...bind("source_ref")} maxLength={500} placeholder="Ticket, data-room folder, or email thread" />
          </div>
        </div>
        {error && (
          <p className="errline" role="alert" style={{ marginTop: 14 }}>
            ✕ {error}
          </p>
        )}
      </form>
    </Modal>
  );
}
