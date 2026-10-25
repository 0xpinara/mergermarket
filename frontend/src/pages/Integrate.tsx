import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, type IntegOverview } from "../lib/api";
import { useAppState } from "../lib/store";
import { Empty, ErrorBox, Kicker, Loading, useToast } from "../components/ui";
import { useRouteDeal } from "./Workspace";
import { STEPS, Stepper, firstOpenStep, type StepKey } from "../components/integrate/Stepper";
import { ProfileView } from "../components/integrate/ProfileView";
import { MapTables, ReviewQueue } from "../components/integrate/MapView";
import { FieldDrawer } from "../components/integrate/FieldDrawer";
import { SimulateView } from "../components/integrate/SimulateView";
import { ExportView } from "../components/integrate/ExportView";
import { VerifyView } from "../components/integrate/VerifyView";
import { useInvalidateInteg } from "../components/integrate/shared";

function TemplatePicker({ dealId, ov }: { dealId: string; ov: IntegOverview }) {
  const { reviewer } = useAppState();
  const toast = useToast();
  const invalidate = useInvalidateInteg(dealId);
  const [pick, setPick] = useState(ov.templates[0]?.id ?? "");
  const create = useMutation({
    mutationFn: () => api.integ.createPlan(dealId, pick, reviewer || "Reviewer", ov.has_plan),
    onSuccess: (p) => { toast(`Plan v${p.version}: ${p.counts.total} columns proposed, ${p.counts.needs_review} need a person`); invalidate(); },
    onError: (e: Error) => toast(e.message, "err"),
  });
  return (
    <section className="card ticks">
      <div className="card-head"><h3>{ov.has_plan ? "Rebuild from a template" : "Choose the target import template"}</h3></div>
      <div className="card-body stack tight">
        <div className="tpl-list" role="radiogroup" aria-label="Target template">
          {ov.templates.map((t) => (
            <label key={t.id} className={`tpl${pick === t.id ? " on" : ""}${t.available === false ? " off" : ""}`}>
              <input type="radio" name="tpl" value={t.id} checked={pick === t.id} disabled={t.available === false}
                     onChange={() => setPick(t.id)} />
              <span className="tn">{t.name}</span>
              <span className="muted small">
                {t.source === "bundled" ? `${t.tables} tables · ${t.columns} columns · ${t.version}${t.synthetic ? " · fictional" : ""}` : t.note}
              </span>
            </label>
          ))}
        </div>
        {ov.has_plan && <p className="warnline"><span aria-hidden="true">!</span><span>Rebuilding replaces the suggestions and clears decisions (the log keeps them).</span></p>}
        <div className="btn-row">
          <button className="btn primary" disabled={!pick || create.isPending || !ov.has_legacy} onClick={() => create.mutate()}>
            {create.isPending ? "Profiling and mapping…" : ov.has_plan ? "Rebuild plan" : "Propose a mapping plan"}
          </button>
          <span className="muted small">Suggestions only — nothing is used until a named reviewer approves it.</span>
        </div>
      </div>
    </section>
  );
}

function DriftBanner({ dealId }: { dealId: string }) {
  const { reviewer } = useAppState();
  const toast = useToast();
  const invalidate = useInvalidateInteg(dealId);
  const q = useQuery({ queryKey: ["integ", dealId, "plan"], queryFn: () => api.integ.plan(dealId) });
  const rebase = useMutation({
    mutationFn: () => api.integ.rebase(dealId, reviewer || "Reviewer"),
    onSuccess: () => { toast("Plan rebased on the new exports"); invalidate(); },
    onError: (e: Error) => toast(e.message, "err"),
  });
  const d = q.data?.drift;
  if (!d?.changed) return null;
  return (
    <div className="banner danger" role="alert">
      <span className="ico" aria-hidden="true">≠</span>
      <span>
        The legacy exports changed since this plan was built (contract {d.plan_fp.slice(0, 8)} → {d.current_fp.slice(0, 8)}).
        {d.issues.length > 0 && <> {d.issues.slice(0, 2).map((x) => x.issue).join(" ")}</>}
      </span>
      <button className="btn sm ink" onClick={() => rebase.mutate()} disabled={rebase.isPending}>Review &amp; rebase</button>
    </div>
  );
}

function PlanSteps({ dealId, step, ov }: { dealId: string; step: StepKey; ov: IntegOverview }) {
  const [open, setOpen] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["integ", dealId, "plan"], queryFn: () => api.integ.plan(dealId), enabled: ov.has_plan });
  if (!ov.has_plan) return <TemplatePicker dealId={dealId} ov={ov} />;
  if (q.isLoading) return <Loading label="Loading the plan" />;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.refetch()} />;
  const plan = q.data!;
  return (
    <>
      {step === "map" && (
        <div className="stack">
          <div className="plan-meta mono small">
            <span>{plan.template.name}</span><span>·</span><span>plan v{plan.version}</span><span>·</span>
            <span title={plan.plan_hash}>#{plan.plan_hash.slice(0, 12)}</span><span>·</span>
            <span>{plan.counts.approved} approved · {plan.counts.needs_review} contested · {plan.counts.suggested} suggested</span>
          </div>
          <MapTables plan={plan} onOpen={setOpen} />
          <details className="card pad"><summary className="small">Rebuild suggestions from a different template…</summary>
            <div style={{ marginTop: 12 }}><TemplatePicker dealId={dealId} ov={ov} /></div></details>
        </div>
      )}
      {step === "review" && <ReviewQueue dealId={dealId} plan={plan} onOpen={setOpen} />}
      {step === "simulate" && <SimulateView dealId={dealId} plan={plan} hasDryRun={!!ov.dry_run} />}
      {open && <FieldDrawer dealId={dealId} itemId={open} onClose={() => setOpen(null)} />}
    </>
  );
}

export function Integrate() {
  const id = useRouteDeal();
  const [params, setParams] = useSearchParams();
  const ov = useQuery({ queryKey: ["integ", id], queryFn: () => api.integ.overview(id) });
  if (ov.isLoading) return <div className="page"><Loading label="Opening the integration plan" /></div>;
  if (ov.error) return <div className="page"><ErrorBox error={ov.error} onRetry={() => ov.refetch()} /></div>;
  const o = ov.data!;
  const asked = params.get("step") as StepKey | null;
  const step: StepKey = asked && STEPS.some((s) => s.key === asked) ? asked : firstOpenStep(o);
  const go = (s: StepKey) => setParams((p) => { p.set("step", s); return p; }, { replace: false });
  const meta = STEPS.find((s) => s.key === step)!;

  return (
    <div className="page integrate">
      <div className="page-head">
        <div>
          <Kicker sec="03">Integrate · plan → dry run → verify</Kicker>
          <h1 className="page-title">Rehearse the move <em>before</em> you make it</h1>
          <p className="page-sub">
            Map the legacy exports onto the target’s import template, simulate the import without touching anything, sign off,
            then verify what actually loaded. Deterministic engine; people make every decision.
          </p>
        </div>
      </div>
      {!o.has_legacy ? (
        <Empty art={" ┌─────────┐\n │ legacy? │\n └────┬────┘\n      ∅"} title="No legacy exports yet"
               actions={<Link className="btn primary" to={`/deals/${id}/workspace`}>Add exports in the Audit Workspace</Link>}>
          The plan is built from the legacy system’s own exports.
        </Empty>
      ) : (
        <>
          <Stepper ov={o} step={step} onStep={go} />
          {o.has_plan && <DriftBanner dealId={id} />}
          <div className="step-head">
            <span className="n mono">{meta.n}</span>
            <h2 className="serif">{meta.label}</h2>
            <span className="muted">{meta.hint}</span>
          </div>
          {step === "profile" && <ProfileView dealId={id} />}
          {(step === "map" || step === "review" || step === "simulate") && <PlanSteps dealId={id} step={step} ov={o} />}
          {step === "export" && (o.has_plan ? <ExportView dealId={id} readiness={o.readiness} /> : <TemplatePicker dealId={id} ov={o} />)}
          {step === "verify" && (o.has_plan ? <VerifyView dealId={id} hasReceipt={!!o.receipt} /> : <TemplatePicker dealId={id} ov={o} />)}
        </>
      )}
    </div>
  );
}
