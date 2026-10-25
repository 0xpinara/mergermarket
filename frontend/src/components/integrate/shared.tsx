import type { ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Confidence, Gate, ItemStatus, PlanItem, Transform } from "../../lib/api";

export const ITEM_STATUS: Record<ItemStatus, { label: string; tone: string; g: string }> = {
  suggested: { label: "suggested", tone: "muted", g: "○" },
  needs_review: { label: "needs review", tone: "amber", g: "?" },
  approved: { label: "approved", tone: "lime", g: "●" },
  rejected: { label: "rejected", tone: "coral", g: "✕" },
  ignored: { label: "ignored", tone: "teal", g: "◇" },
  unmapped: { label: "no source", tone: "coral", g: "∅" },
};

export const CONFIDENCE: Record<Confidence, { label: string; cls: string; hint: string }> = {
  exact: { label: "audit-bound", cls: "hi", hint: "The audit's own mapping binds this column, and nothing contradicts it." },
  derived: { label: "derived", cls: "hi", hint: "Built the same way the audit builds it (e.g. first + last name)." },
  conflict: { label: "contested", cls: "mid", hint: "Evidence disagrees, or two sources are close. A person decides." },
  weak: { label: "weak", cls: "mid", hint: "Only a name or AI hint supports it. Confirm by hand." },
  none: { label: "—", cls: "", hint: "No compatible source column." },
};

export const TYPE_GLYPH: Record<string, string> = {
  key: "⚿", ref: "↗", money: "$", date: "▦", enum: "≡", text: "Aa", name: "☺", unit: "#", int: "9", email: "@",
  phone: "☏",
};

export function ItemChip({ status }: { status: ItemStatus }) {
  const s = ITEM_STATUS[status];
  return (
    <span className={`chip tone-${s.tone}`}>
      <span aria-hidden="true">{s.g}</span> {s.label}
    </span>
  );
}

export function GateMark({ status }: { status: Gate["status"] }) {
  const t = { pass: ["✓", "pass"], fail: ["✕", "fail"], pending: ["·", "pending"] }[status];
  return <span className={`mark ${status === "pending" ? "" : status}`}>{t[0]} {t[1].toUpperCase()}</span>;
}

/** Plain-English rendering of an allowlisted transform. Mirrors app/integrate/transforms.describe. */
export function describe(t: Transform | null, sources: string[]): string {
  if (!t) return "—";
  const src = sources.map((s) => `\`${s}\``).join(" + ");
  switch (t.op) {
    case "rekey":
      return `new key ${t.prefix as string}${"0".repeat((t.width as number) - 1)}1… from ${src}`;
    case "ref_lookup":
      return `${src} → new ${t.table as string} key`;
    case "date_format":
      return `${src} as ${t.format as string}`;
    case "enum_map":
      return `${src} mapped: ${Object.entries(t.values as Record<string, string>).map(([k, v]) => `${k}→${v}`).join(", ")}`;
    case "constant":
      return `always “${t.value as string}”`;
    case "concat":
      return `join ${src}`;
    case "money":
      return `${src} · exact cents`;
    default:
      return `${src} · ${t.op.replace("_", " ")}`;
  }
}

export function Code({ children }: { children: ReactNode }) {
  return <code className="code">{children}</code>;
}

/** Render text with `backtick` spans as code. Used for server-written reasons. */
export function Ticks({ text }: { text: string }) {
  const parts = text.split(/(`[^`]+`)/g);
  return (
    <>
      {parts.map((p, i) => (p.startsWith("`") && p.endsWith("`") ? <Code key={i}>{p.slice(1, -1)}</Code> : p))}
    </>
  );
}

export function useInvalidateInteg(dealId: string) {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["integ", dealId] });
    qc.invalidateQueries({ queryKey: ["summary", dealId] });
  };
}

export function itemNeedsDecision(i: PlanItem): boolean {
  return i.status !== "approved" && i.status !== "ignored";
}

export function decisionOrder(i: PlanItem): number {
  if (i.status === "needs_review" || i.status === "unmapped" || i.status === "rejected") return 0;
  if (i.financial && itemNeedsDecision(i)) return 1;
  if (itemNeedsDecision(i)) return 2;
  return 3;
}

export function Hazard({ children }: { children: ReactNode }) {
  return (
    <div className="hazard" role="note">
      <span className="tape" aria-hidden="true" />
      <span className="msg">{children}</span>
      <span className="tape" aria-hidden="true" />
    </div>
  );
}
