import type { IntegOverview } from "../../lib/api";

export type StepKey = "profile" | "map" | "review" | "simulate" | "export" | "verify";
type StepState = "done" | "warn" | "blocked" | "todo";

export const STEPS: { key: StepKey; n: string; label: string; hint: string }[] = [
  { key: "profile", n: "01", label: "Profile", hint: "what the legacy files hold" },
  { key: "map", n: "02", label: "Map", hint: "legacy → target columns" },
  { key: "review", n: "03", label: "Review", hint: "a person decides each one" },
  { key: "simulate", n: "04", label: "Simulate", hint: "dry run · nothing imported" },
  { key: "export", n: "05", label: "Export", hint: "sign off · download" },
  { key: "verify", n: "06", label: "Verify", hint: "after the real load" },
];

const GLYPH: Record<StepState, string> = { done: "●", warn: "◐", blocked: "✕", todo: "○" };

export function stepStates(ov: IntegOverview): Record<StepKey, { state: StepState; note: string }> {
  const g = Object.fromEntries((ov.readiness.gates ?? []).map((x) => [x.id, x.status]));
  const c = ov.plan?.counts;
  const left = c ? c.total - c.decided : 0;
  const sim = ["G4", "G5", "G6", "G7"].map((k) => g[k]);
  return {
    profile: ov.has_legacy ? { state: "done", note: "contract fingerprinted" } : { state: "todo", note: "add legacy exports" },
    map: ov.has_plan ? { state: "done", note: `${c?.total ?? 0} columns proposed` } : { state: "todo", note: "pick a template" },
    review: !ov.has_plan ? { state: "todo", note: "—" }
      : left === 0 ? { state: "done", note: "every column decided" } : { state: "warn", note: `${left} to decide` },
    simulate: !ov.dry_run ? { state: "todo", note: "not run yet" }
      : sim.every((s) => s === "pass") ? { state: "done", note: "clean dry run" }
      : g.G4 !== "pass" ? { state: "warn", note: "plan changed · rerun" }
      : { state: "blocked", note: `${ov.dry_run.totals.rejected} rows rejected` },
    export: g.G8 === "pass" ? { state: "done", note: "signed off" }
      : ov.readiness.signoff_ready ? { state: "warn", note: "ready to sign" } : { state: "todo", note: "locked" },
    verify: ov.readiness.state === "released" ? { state: "done", note: "receipt issued" }
      : ov.readiness.release_ready ? { state: "warn", note: "ready for receipt" }
      : g.G9 === "pass" ? { state: "blocked", note: "findings open" } : { state: "todo", note: "after the load" },
  };
}

export function firstOpenStep(ov: IntegOverview): StepKey {
  const st = stepStates(ov);
  return STEPS.find((s) => st[s.key].state !== "done")?.key ?? "verify";
}

export function Stepper({ ov, step, onStep }: { ov: IntegOverview; step: StepKey; onStep: (s: StepKey) => void }) {
  const st = stepStates(ov);
  return (
    <nav className="rail" aria-label="Integration steps">
      <ol>
        {STEPS.map((s) => (
          <li key={s.key} className={`rs-${st[s.key].state}${step === s.key ? " current" : ""}`}>
            <button type="button" onClick={() => onStep(s.key)} aria-current={step === s.key ? "step" : undefined}>
              <span className="n">{s.n}</span>
              <span className="l">{s.label}</span>
              <span className="st">
                <span aria-hidden="true">{GLYPH[st[s.key].state]}</span> {st[s.key].note}
              </span>
            </button>
          </li>
        ))}
      </ol>
    </nav>
  );
}
