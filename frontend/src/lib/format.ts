import type { FindingStatus, Severity } from "./api";

export function money(cents: number | null | undefined, opts: { decimals?: boolean; signed?: boolean } = {}): string {
  if (cents === null || cents === undefined) return "—";
  const decimals = opts.decimals ?? false;
  const abs = Math.abs(cents) / 100;
  const s = abs.toLocaleString("en-US", {
    minimumFractionDigits: decimals ? 2 : 0,
    maximumFractionDigits: decimals ? 2 : 0,
  });
  const sign = cents < 0 ? "−" : opts.signed && cents > 0 ? "+" : "";
  return `${sign}$${s}`;
}

export function num(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("en-US");
}

export function pct(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined) return "—";
  return `${n.toFixed(digits)}%`;
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}

export function day(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return day(iso);
}

export const SEVERITY_LABEL: Record<Severity, string> = { critical: "CRIT", high: "HIGH", medium: "MED", low: "LOW" };
export const SEVERITY_ORDER: Record<Severity, number> = { critical: 0, high: 1, medium: 2, low: 3 };

export const STATUS_LABEL: Record<FindingStatus, string> = {
  open: "Open",
  investigating: "Investigating",
  expected: "Expected exception",
  resolved: "Resolved",
};

export const STATUS_GLYPH: Record<FindingStatus, string> = { open: "○", investigating: "◐", expected: "◇", resolved: "●" };

export const ENTITY_LABEL: Record<string, string> = {
  leases: "Leases",
  tenants: "Tenants",
  deposits: "Deposits",
  maintenance: "Maintenance",
  properties: "Properties",
  units: "Units",
  data_quality: "Data quality",
};

export const DEAL_STATE: Record<string, { label: string; tone: string }> = {
  draft: { label: "Draft · add exports", tone: "muted" },
  needs_input: { label: "Needs mapping", tone: "amber" },
  ready: { label: "Ready to audit", tone: "lime" },
  stale: { label: "Changed · rerun", tone: "amber" },
  audited: { label: "Audited · review pending", tone: "coral" },
  in_review: { label: "In review", tone: "amber" },
  complete: { label: "All findings closed", tone: "lime" },
};

export function plural(n: number, one: string, many?: string): string {
  return `${num(n)} ${n === 1 ? one : many || one + "s"}`;
}
