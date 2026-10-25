export type Severity = "critical" | "high" | "medium" | "low";
export type FindingStatus = "open" | "investigating" | "expected" | "resolved";
export type Side = "before" | "after";

export interface MappingMeta {
  column: string;
  confidence: number;
  method: string;
}

export interface Validation {
  status: "ready" | "needs_mapping";
  missing: string[];
  problems: string[];
  warnings: string[];
  stats: { rows: number; blank_ids: number; duplicate_ids: number; bad_money: number; bad_dates: number };
}

export interface Snapshot {
  id: string;
  side: Side;
  entity: string;
  entity_label: string;
  origin: "seed" | "upload";
  file_name: string;
  sheet: string | null;
  headers: string[];
  row_count: number;
  mapping: Record<string, string>;
  mapping_meta: Record<string, MappingMeta>;
  detected_entity: string;
  entity_confidence: number;
  user_mapped: boolean;
  warnings: string[];
  validation: Validation;
  ai_suggestions: Record<string, { column: string; similarity: number; runner_up: number; method: string; origin: "ai" }>;
  preview: { row: number; values: string[] }[];
  created_at: string;
  updated_at: string;
}

export interface DealStatus {
  state: "draft" | "needs_input" | "ready" | "stale" | "audited" | "in_review" | "complete";
  problems: string[];
  skipped: string[];
  last_run_at: string | null;
  open_findings: number;
  closed_findings: number;
}

export interface Deal {
  id: string;
  title: string;
  industry: string | null;
  acquirer: string | null;
  target_business: string | null;
  deal_date: string | null;
  old_system: string | null;
  new_system: string | null;
  source_notes: string | null;
  source_ref: string | null;
  is_sample: boolean;
  created_at: string;
  updated_at: string;
  status: DealStatus;
  snapshots?: Snapshot[];
  materiality_rent_cents?: number | null;
  materiality_deposit_cents?: number | null;
}

export interface Stage {
  name: string;
  detail: string;
  ms: number;
}

export interface Check {
  id: string;
  label: string;
  before: number;
  after: number;
  kind: "count" | "money";
  delta: number | null;
  status: "pass" | "warn" | "fail";
  note: string;
}

export interface EntityCount {
  before: number;
  after: number;
  confirmed?: number;
  probable?: number;
  reviewer_confirmed?: number;
  needs_review?: number;
  unmatched?: number;
  target_unmatched?: number;
}

export interface RentLine {
  risk_key: string;
  finding_id: string;
  rule_id: string;
  title: string;
  subject: string;
  cents: number;
  also: { finding_id: string; rule_id: string; title: string; cents: number }[];
}

export interface Brief {
  id: string;
  rule_id: string;
  title: string;
  severity: Severity;
  entity: string;
  subject: string;
  status: FindingStatus;
  rent_risk_cents: number;
  deposit_cents: number;
  pending_rent_cents: number;
  explanation: string;
  rent_counted_cents: number;
  rent_covered_by: string | null;
}

export interface RuleRun {
  rule_id: string;
  name: string;
  check: string;
  entity: string;
  default_severity: Severity;
  records_checked: number;
  findings: number;
}

export interface Labels {
  old: string;
  new: string;
  old_short: string;
  new_short: string;
  old_system: string;
  new_system: string;
}

export interface Summary {
  has_run: boolean;
  status?: DealStatus & { fingerprint: string | null };
  run?: {
    id: string;
    started_at: string;
    finished_at: string;
    engine_version: string;
    fingerprint: string;
    stages: Stage[];
    stale: boolean;
    match_decisions_pending: number;
  };
  labels?: Labels;
  ai?: AiStatus & { suggestions: number; decisions_by_ai: number; used_for: string[] };
  as_of?: string;
  counts?: Record<string, EntityCount>;
  checks?: Check[];
  benign?: Record<string, { count: number; examples: [string, string][] }>;
  rules?: RuleRun[];
  skipped?: string[];
  files?: { side: Side; entity: string; file: string; rows: number }[];
  totals?: { active_leases: number; active_rent: number; source_records: number; target_records: number };
  kpis?: {
    records_total: number;
    records_verified: number;
    records_needs_review: number;
    critical_open: number;
    high_open: number;
    rent_at_risk_open: number;
    rent_at_risk_detected: number;
    deposit_open: number;
    deposit_detected: number;
    deposit_open_count: number;
    pending_review_rent: number;
    findings_total: number;
    findings_open: number;
    findings_closed: number;
    needs_rereview: number;
    material_open?: number;
  };
  materiality?: { rent_cents: number | null; deposit_cents: number | null; note: string };
  continuity?: {
    active_leases: number;
    matched: number;
    accounted: number;
    clean: number;
    score: number | null;
    clean_pct: number | null;
  };
  severity_open?: Record<Severity, number>;
  status_counts?: Record<string, number>;
  rent_lines?: RentLine[];
  attention?: Brief[];
}

export interface AiStatus {
  enabled: boolean;
  available: boolean;
  model: string | null;
  reason: string | null;
  local: boolean;
  dim?: number | null;
}

export interface AiCard {
  status: AiStatus;
  does: string[];
  never: string[];
  off_switch: string;
  evaluation: null | {
    generated_at: string;
    rules_only: EvalRun;
    with_ai: EvalRun | null;
    headers: { rules_only: { summary: HeaderTally }; with_ai: { summary: HeaderTally } | null };
  };
}

export interface HeaderTally { correct: number; wrong: number; abstained: number; total: number; accuracy: number }

export interface EvalRun {
  seeds: number;
  ai_model: string | null;
  anomalies: { injected: number; detected: number; recall: number; false_alarms: number; routed_to_review: number;
               silently_missed: number; missed_but_flagged_for_review: number };
  matching: Record<string, { precision: number; recall: number; pairs: number }>;
  money: { rent_at_risk_exact: number; deposit_exact: number; of: number };
  ai_work_orders: { rewrites: number; top1: number; any: number; dropped: number; spurious: number };
  ms_per_audit: number;
}

export interface Ref {
  file: string;
  row: number;
  id: string | null;
}

export interface FindingRow {
  id: string;
  rule_id: string;
  rule_name: string;
  title: string;
  severity: Severity;
  entity: string;
  subject: string;
  status: FindingStatus;
  resolution: string | null;
  active: boolean;
  needs_rereview: boolean;
  confidence: string;
  rent_risk_cents: number;
  deposit_cents: number;
  pending_rent_cents: number;
  impact_type: string;
  impact_cents: number;
  rent_counted_cents: number;
  rent_covered_by: string | null;
  source_ref: Ref | null;
  target_ref: Ref | null;
  keys: Record<string, string | null>;
  updated_at: string;
  explanation: string;
  material?: boolean;
  material_reason?: string | null;
}

export interface EvidenceRecord {
  key: string;
  id: string | null;
  file: string;
  row: number;
  side: Side;
  entity: string;
  raw: Record<string, string>;
  issues: { field: string; kind: string; value: string }[];
  context: Record<string, unknown>;
}

export interface CompareRow {
  field: string;
  label: string;
  kind: string;
  before: { raw: string | null; value: string | null } | null;
  after: { raw: string | null; value: string | null } | null;
  state: "same" | "format" | "rekeyed" | "differs" | "missing" | "na";
}

export interface Candidate {
  target_key: string;
  origin?: "ai";
  similarity?: number;
  score: number;
  reasons: [string, number][];
  notes: string[];
  taken_by: string | null;
  record: EvidenceRecord;
  summary: Record<string, string | null>;
}

export interface ReviewEvent {
  id: string;
  reviewer: string;
  action: string;
  note: string | null;
  from_status: string | null;
  to_status: string | null;
  payload: Record<string, unknown> | null;
  created_at: string;
}

export interface FindingDetail extends FindingRow {
  deal_id: string;
  labels: Labels;
  rule: { name: string; entity: string; severity: Severity; check: string };
  payload: {
    explanation: string;
    recommended_action: string;
    source: EvidenceRecord | null;
    target: EvidenceRecord | null;
    extra_targets: EvidenceRecord[];
    compare: CompareRow[];
    match: { status: string; score: number; reasons: [string, number][]; notes: string[]; method: string } | null;
    candidates: Candidate[];
    impact: { type: string; cents: number; monthly?: boolean; label?: string; calc?: [string, string][] };
    members: Record<string, string | number | null>[];
  };
  events: ReviewEvent[];
  related: { id: string; rule_id: string; title: string; severity: Severity; status: FindingStatus;
             rent_risk_cents: number; deposit_cents: number }[];
  override: { target_key: string; reviewer: string; created_at: string } | null;
  created_at: string;
}

export interface UnitCell {
  key: string;
  label: string;
  norm: string | null;
  floor: number | null;
  lease: string | null;
  tenant: string | null;
  rent: number | null;
  state: "verified" | "issue" | "critical" | "review" | "reviewed" | "vacant";
  findings: string[];
  top: { id: string; title: string } | null;
}

export interface UnitMap {
  has_run: boolean;
  properties: { key: string; name: string; address: string | null; units: UnitCell[] }[];
}

export interface IntelNote {
  id: string;
  company_name: string;
  sector: string | null;
  location: string | null;
  event_date: string | null;
  website: string | null;
  source_link: string | null;
  notes: string | null;
  contact_role: string | null;
  pilot_status: string;
  linked_deal_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface SchemaField {
  key: string;
  label: string;
  kind: string;
  required: boolean;
}

export interface SchemaEntity {
  key: string;
  label: string;
  singular: string;
  description: string;
  id_field: string;
  one_of: string[][];
  fields: SchemaField[];
}

export interface SchemaInfo {
  entities: SchemaEntity[];
  limits: { max_upload_mb: number; max_rows_per_file: number; max_files_per_request: number; file_types: string[] };
}

// --------------------------------------------------------------------------- client

// --------------------------------------------------------------------------- integrate (plan → dry run → verify)

export type ItemStatus = "suggested" | "needs_review" | "approved" | "rejected" | "ignored" | "unmapped";
export type Confidence = "exact" | "derived" | "conflict" | "weak" | "none";

export interface Transform {
  op: string;
  [k: string]: unknown;
}

export interface Signal {
  kind: "audit" | "name" | "references" | "ai" | "template";
  points: number;
  detail: string;
  ratio?: number;
}

export interface PlanCandidate {
  sources: string[];
  transform: Transform;
  score: number;
  signals: Signal[];
}

export interface Decision {
  by: string;
  at: string;
  action: string;
  note: string | null;
}

export interface PlanItem {
  id: string;
  table: string;
  column: string;
  type: string;
  canonical: string | null;
  required: boolean;
  financial: boolean;
  description: string;
  sources: string[];
  transform: Transform | null;
  status: ItemStatus;
  confidence: Confidence;
  reasons: string[];
  candidates: PlanCandidate[];
  excluded: { sources: string[]; reason: string; signals: string[] }[];
  warnings: string[];
  live_warnings: string[];
  decision: Decision | null;
  bulk_eligible: boolean;
  needs_note: boolean;
  enum_coverage?: {
    mapped: Record<string, { canonical: string; target: string; rows: number }>;
    unmapped: Record<string, { canonical: string | null; rows: number }>;
  };
}

export interface TemplateColumn {
  name: string;
  type: string;
  canonical: string | null;
  required: boolean;
  financial: boolean;
  description: string;
  max_length?: number;
  ref?: string;
  format?: string;
  values?: Record<string, string | null>;
  vocabulary?: string | null;
}

export interface TemplateTable {
  name: string;
  entity: string;
  file: string;
  key: string;
  key_prefix: string;
  columns: TemplateColumn[];
}

export interface PlanBinding {
  name: string;
  entity: string;
  file: string;
  snapshot_id: string | null;
  source_file: string | null;
  rows: number;
  unused: string[];
  note: string | null;
}

export interface PlanCounts {
  total: number;
  approved: number;
  ignored: number;
  needs_review: number;
  suggested: number;
  rejected: number;
  unmapped: number;
  bulk_eligible: number;
  financial_pending: number;
  required_open: number;
  decided: number;
}

export interface Drift {
  changed: boolean;
  plan_fp: string;
  current_fp: string;
  issues: { table: string; item?: string; issue: string }[];
}

export interface Plan {
  id: string;
  template: { id: string; name: string; version: string; notes: string; synthetic: boolean; source: string };
  template_tables: TemplateTable[];
  version: number;
  plan_hash: string;
  contract_fp: string;
  tables: PlanBinding[];
  items: PlanItem[];
  counts: PlanCounts;
  drift: Drift;
  created_at: string;
  updated_at: string;
}

export interface TemplateInfo {
  id: string;
  name: string;
  version: string;
  source: "bundled" | "derived";
  synthetic?: boolean;
  tables?: number;
  columns?: number;
  available?: boolean;
  note?: string;
}

export interface ProfileColumn {
  column: string;
  type: string;
  type_support: number;
  rows: number;
  blank: number;
  distinct: number;
  zero: number;
  unique: boolean;
  complete: boolean;
  patterns: { shape: string; count: number }[];
  min_length: number;
  max_length: number;
  pii: boolean;
  values?: { value: string; count: number }[];
  examples?: string[];
  key_candidate: boolean;
  mapped_to: string | null;
}

export interface ProfileFile {
  snapshot_id: string;
  file: string;
  sheet: string | null;
  entity: string;
  entity_label: string;
  rows: number;
  columns: ProfileColumn[];
  content_hash: string;
  primary_key: string | null;
  keys: string[];
}

export interface Relationship {
  from: { file: string; entity: string; column: string };
  to: { file: string; entity: string; column: string };
  inclusion: number;
  values: number;
  missing: number;
}

export interface SourceProfile {
  fingerprint: string;
  files: ProfileFile[];
  relationships: Relationship[];
}

export interface Gate {
  id: string;
  phase: "plan" | "simulate" | "approve" | "verify";
  label: string;
  status: "pass" | "fail" | "pending";
  detail: string;
}

export interface Signoff {
  id: string;
  plan_hash: string;
  dry_run_id: string;
  reviewer: string;
  statement: string;
  created_at: string;
  revoked_at: string | null;
  revoked_by: string | null;
}

export interface Readiness {
  has_plan: boolean;
  gates: Gate[];
  signoff_ready: boolean;
  export_ready: boolean;
  release_ready: boolean;
  state: "no_plan" | "planning" | "simulating" | "ready_to_sign" | "verifying" | "ready_to_release" | "released";
  plan_hash?: string;
  plan_version?: number;
  dry_run_id?: string | null;
  signoff?: Signoff | null;
}

export interface PreflightGroup {
  check: string;
  label: string;
  table: string;
  column: string;
  blocking: boolean;
  rows: number;
  examples: { file: string; row: number; value: string | null; detail: string | null;
              cause?: { check: string; table: string; column: string } }[];
}

export interface Blast {
  check: string;
  label: string;
  table: string;
  column: string;
  rows: Record<string, number>;
  rows_total: number;
  active_leases: number;
  rent_cents: number;
  deposit_cents: number;
  open_work_orders: number;
}

export interface Predicted {
  id: string;
  anchor: string;
  rule_id: string;
  severity: Severity;
  title: string;
  entity: string;
  subject: string;
  rent_risk_cents: number;
  deposit_cents: number;
  pending_rent_cents: number;
  explanation: string;
  source: Ref | null;
  target: Ref | null;
}

export interface DryRun {
  id: string;
  created_at: string;
  plan_hash: string;
  plan_version: number;
  contract_fp: string;
  output_hash: string;
  banner: string;
  importer_model: string;
  current?: boolean;
  template: { id: string; name: string; version: string };
  plan_status: PlanCounts;
  provisional: number;
  tables: { table: string; file: string; source_file: string | null; source_rows: number; accepted: number;
            rejected: number }[];
  totals: { source_rows: number; accepted: number; rejected: number; blocking_checks: number; warnings: number };
  preflight: PreflightGroup[];
  blast_radius: Blast[];
  tieouts: Check[];
  properties: { property: string; legacy_id: string | null; legacy: { active_leases: number; rent_cents: number };
                simulated: { active_leases: number; rent_cents: number } | null; ties: boolean }[];
  predicted: Predicted[];
  predicted_kpis: { findings: number; critical: number; high: number; medium: number; low: number;
                    rent_at_risk_cents: number; deposit_cents: number };
  diff: null | {
    previous_id: string;
    previous_version: number;
    preflight: { resolved: string[]; new: string[]; changed: string[] };
    predicted: { resolved: number; new: number; unchanged: number };
    rejected_rows: { before: number; after: number };
    rent_at_risk: { before: number; after: number };
  };
  files: Record<string, { file: string; rows: number; sha256: string }>;
}

export interface SimPreview {
  banner: string;
  table: string;
  file?: string;
  columns: string[];
  rows?: Record<string, string>[];
  lineage?: { row: number; file: string; legacy_key: string | null; columns: Record<string, { sources: string[]; op: string }> }[];
  rejected?: { row: number; root: { check: string; table: string; column: string };
               problems: { check: string; column: string; detail: string | null }[] }[];
  total: number;
  rejected_total?: number;
  offset: number;
}

export interface ObservedRow {
  finding_id: string;
  rule_id: string;
  severity: Severity;
  title: string;
  subject: string;
  status: FindingStatus;
  open: boolean;
  rent_risk_cents: number;
  deposit_cents: number;
  rent_counted_cents: number;
}

export interface Comparison {
  available: boolean;
  has_dry_run?: boolean;
  has_audit?: boolean;
  dry_run_id?: string;
  dry_run_at?: string;
  run_id?: string;
  run_at?: string;
  counts?: { predicted: number; observed: number; both: number; observed_only: number; predicted_only: number };
  observed_only?: ObservedRow[];
  both?: ObservedRow[];
  predicted_only?: { rule_id: string; severity: Severity; title: string; subject: string; rent_risk_cents: number;
                     deposit_cents: number }[];
  tieouts?: { id: string; label: string; kind: "count" | "money"; legacy: number; simulated: number;
              actual: number | null; simulated_ties: boolean; actual_ties: boolean }[];
  legacy_changed?: boolean;
  note?: string;
}

export interface ReceiptDoc {
  id: string;
  sha256: string;
  verifies?: boolean;
  receipt: {
    receipt: string;
    deal: { id: string; title: string; synthetic: boolean; legacy_system: string | null; target_system: string | null };
    issued_at: string;
    issued_by: string;
    engine_version: string;
    template: { id: string; name: string; version: string };
    plan: { hash: string; version: number; source_contract: string };
    dry_run: { id: string; at: string; simulated_output_sha256: string; rows: DryRun["totals"];
               predicted: DryRun["predicted_kpis"] };
    signoff: { reviewer: string; at: string; statement: string; kind: string };
    export: { manifest_sha256: string; files: Record<string, string> };
    verification: { run_id: string; at: string; fingerprint: string; findings: { total: number; by_status: Record<string, number> };
                    continuity_score: number | null };
    gates: { id: string; label: string; status: string }[];
    limits: string[];
  };
}

export interface IntegOverview {
  has_plan: boolean;
  has_legacy: boolean;
  templates: TemplateInfo[];
  readiness: Readiness;
  audit_state: string;
  banner: string;
  plan?: { version: number; plan_hash: string; counts: PlanCounts; template: { id: string; name: string; version: string } };
  dry_run?: Pick<DryRun, "id" | "created_at" | "totals" | "predicted_kpis" | "plan_hash" | "plan_version">;
  receipt: { id: string; sha256: string; issued_at: string } | null;
}

export interface FieldEvidence {
  item: PlanItem;
  target: TemplateColumn;
  source_file: string | null;
  profile: ProfileColumn[];
  live_warnings: string[];
  needs_note: boolean;
  history: { id: string; actor: string; action: string; note: string | null; created_at: string; version: number;
             before: Partial<PlanItem> | null; after: Partial<PlanItem> | null }[];
}

export interface PlanEvent {
  id: string;
  actor: string;
  action: string;
  item_id: string | null;
  note: string | null;
  version: number;
  plan_hash: string;
  created_at: string;
  before: Partial<PlanItem> | null;
  after: Partial<PlanItem> | null;
}

const WS_KEY = "mp.workspace";
let memoryWs: string | null = null;

function randomId(): string {
  const bytes = new Uint8Array(9);
  crypto.getRandomValues(bytes);
  return "ws_" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** A per-browser workspace id that keeps demo visitors' audits apart. Not an access control. */
export function workspaceId(): string {
  if (memoryWs) return memoryWs;
  try {
    let ws = localStorage.getItem(WS_KEY);
    if (!ws || !/^[A-Za-z0-9_-]{8,64}$/.test(ws)) {
      ws = randomId();
      localStorage.setItem(WS_KEY, ws);
    }
    memoryWs = ws;
  } catch {
    memoryWs = randomId();
  }
  return memoryWs;
}

export class ApiError extends Error {
  status: number;
  problems: string[];
  fileErrors: { file: string; message: string }[];
  constructor(message: string, status: number, problems: string[] = [], fileErrors: { file: string; message: string }[] = []) {
    super(message);
    this.status = status;
    this.problems = problems;
    this.fileErrors = fileErrors;
  }
}

function messageFrom(body: unknown, status: number): { message: string; problems: string[] } {
  const fallback = status >= 500 ? "Something went wrong on our side. Please retry." : `Request failed (${status}).`;
  if (!body || typeof body !== "object") return { message: fallback, problems: [] };
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return { message: detail, problems: [] };
  if (Array.isArray(detail)) {
    const msgs = detail.map((d: { msg?: string; loc?: (string | number)[] }) => {
      const field = d.loc?.filter((x) => x !== "body").join(".");
      return `${field ? field + ": " : ""}${(d.msg || "invalid").replace(/^Value error, /, "")}`;
    });
    return { message: msgs[0] || fallback, problems: msgs };
  }
  if (detail && typeof detail === "object") {
    const d = detail as { message?: string; problems?: string[] };
    return { message: d.message || fallback, problems: d.problems || [] };
  }
  return { message: fallback, problems: [] };
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "X-Workspace": workspaceId() };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { method, headers, body: payload });
  } catch {
    throw new ApiError("Can't reach the MergeProof server. Check your connection and retry.", 0);
  }
  if (res.status === 204) return undefined as T;
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const { message, problems } = messageFrom(data, res.status);
    const fileErrors = (data as { errors?: { file: string; message: string }[] })?.errors || [];
    throw new ApiError(message, res.status, problems, fileErrors);
  }
  return data as T;
}

export const api = {
  schema: () => req<SchemaInfo>("GET", "/schema"),
  ai: () => req<AiCard>("GET", "/ai"),
  deals: () => req<Deal[]>("GET", "/deals"),
  deal: (id: string) => req<Deal>("GET", `/deals/${id}`),
  createDeal: (body: Partial<Deal>) => req<Deal>("POST", "/deals", body),
  updateDeal: (id: string, body: Partial<Deal>) => req<Deal>("PATCH", `/deals/${id}`, body),
  deleteDeal: (id: string) => req<void>("DELETE", `/deals/${id}`),
  sample: (reset = false) => req<{ deal: Deal; created: boolean }>("POST", "/sample", { reset }),
  seedDemo: () => req<{ seeded: boolean; deal_id: string | null }>("POST", "/demo/seed"),
  sampleFiles: () => req<{ side: Side | "messy"; name: string; bytes: number; rows: number }[]>("GET", "/sample/files"),
  upload: (dealId: string, side: Side, files: File[]) => {
    const fd = new FormData();
    fd.append("side", side);
    fd.append("confirm_synthetic", "true");
    files.forEach((f) => fd.append("files", f, f.name));
    return req<{ created: Snapshot[]; errors: { file: string; message: string }[] }>("POST", `/deals/${dealId}/snapshots`, fd);
  },
  updateMapping: (sid: string, entity: string, mapping: Record<string, string | null>) =>
    req<Snapshot>("PATCH", `/snapshots/${sid}`, { entity, mapping }),
  suggest: (sid: string, entity: string) =>
    req<{ entity: string; mapping: Record<string, string>; meta: Record<string, MappingMeta> }>("POST", `/snapshots/${sid}/suggest?entity=${entity}`),
  deleteSnapshot: (sid: string) => req<void>("DELETE", `/snapshots/${sid}`),
  runAudit: (dealId: string) => req<{ run: { run_id: string; stages: Stage[] }; summary: Summary }>("POST", `/deals/${dealId}/audit`),
  summary: (dealId: string) => req<Summary>("GET", `/deals/${dealId}/summary`),
  findings: (dealId: string, includeInactive = false) =>
    req<FindingRow[]>("GET", `/deals/${dealId}/findings${includeInactive ? "?include_inactive=true" : ""}`),
  finding: (fid: string) => req<FindingDetail>("GET", `/findings/${fid}`),
  review: (fid: string, body: { action: string; reviewer: string; note?: string; candidate?: string }) =>
    req<FindingDetail>("POST", `/findings/${fid}/review`, body),
  reviews: (dealId: string) =>
    req<(ReviewEvent & { finding_id: string; title: string; subject: string; rule_id: string; severity: Severity })[]>(
      "GET", `/deals/${dealId}/reviews`),
  unitMap: (dealId: string) => req<UnitMap>("GET", `/deals/${dealId}/unitmap`),
  integ: {
    overview: (d: string) => req<IntegOverview>("GET", `/deals/${d}/integrate`),
    profile: (d: string) => req<SourceProfile>("GET", `/deals/${d}/integrate/profile`),
    plan: (d: string) => req<Plan>("GET", `/deals/${d}/integrate/plan`),
    createPlan: (d: string, template_id: string, reviewer: string, replace = false) =>
      req<Plan>("POST", `/deals/${d}/integrate/plan`, { template_id, reviewer, replace }),
    act: (d: string, item: string, body: { action: string; reviewer: string; note?: string | null; sources?: string[];
                                            transform?: Transform }) =>
      req<Plan>("POST", `/deals/${d}/integrate/plan/items/${encodeURIComponent(item)}`, body),
    bulk: (d: string, reviewer: string) =>
      req<{ approved: string[]; plan: Plan }>("POST", `/deals/${d}/integrate/plan/bulk-approve`, { reviewer }),
    rebase: (d: string, reviewer: string) => req<Plan>("POST", `/deals/${d}/integrate/plan/rebase`, { reviewer }),
    evidence: (d: string, item: string) =>
      req<FieldEvidence>("GET", `/deals/${d}/integrate/plan/items/${encodeURIComponent(item)}/evidence`),
    events: (d: string) => req<PlanEvent[]>("GET", `/deals/${d}/integrate/events`),
    dryRun: (d: string, reviewer: string) => req<DryRun>("POST", `/deals/${d}/integrate/dry-run`, { reviewer }),
    latestDryRun: (d: string) => req<DryRun>("GET", `/deals/${d}/integrate/dry-run`),
    preview: (d: string, table: string, offset = 0, limit = 25, rejected = false) =>
      req<SimPreview>("GET", `/deals/${d}/integrate/dry-run/preview?table=${encodeURIComponent(table)}&offset=${offset}` +
                             `&limit=${limit}${rejected ? "&rejected=true" : ""}`),
    readiness: (d: string) => req<Readiness>("GET", `/deals/${d}/integrate/readiness`),
    signoff: (d: string, reviewer: string, acknowledge: boolean) =>
      req<Readiness>("POST", `/deals/${d}/integrate/signoff`, { reviewer, acknowledge }),
    revoke: (d: string, reviewer: string, note?: string) =>
      req<Readiness>("POST", `/deals/${d}/integrate/signoff/revoke`, { reviewer, note }),
    verify: (d: string) => req<{ comparison: Comparison; readiness: Readiness }>("GET", `/deals/${d}/integrate/verify`),
    issueReceipt: (d: string, reviewer: string) => req<ReceiptDoc>("POST", `/deals/${d}/integrate/receipt`, { reviewer }),
    receipt: (d: string) => req<ReceiptDoc>("GET", `/deals/${d}/integrate/receipt`),
  },
  intel: () => req<IntelNote[]>("GET", "/intel"),
  createIntel: (body: Partial<IntelNote>) => req<IntelNote>("POST", "/intel", body),
  updateIntel: (id: string, body: Partial<IntelNote>) => req<IntelNote>("PUT", `/intel/${id}`, body),
  deleteIntel: (id: string) => req<void>("DELETE", `/intel/${id}`),
};

export function downloadUrl(path: string): string {
  return `/api${path}${path.includes("?") ? "&" : "?"}ws=${encodeURIComponent(workspaceId())}`;
}

export function sampleFileUrl(side: Side | "messy", name: string): string {
  return `/api/sample/files/${side}/${encodeURIComponent(name)}`;
}
