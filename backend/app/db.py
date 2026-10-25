"""SQLite persistence. All statements are parameterized."""
from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS deals (
  id TEXT PRIMARY KEY,
  workspace TEXT NOT NULL,
  title TEXT NOT NULL,
  industry TEXT, acquirer TEXT, target_business TEXT, deal_date TEXT,
  old_system TEXT, new_system TEXT, source_notes TEXT, source_ref TEXT,
  is_sample INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS deals_ws ON deals(workspace, updated_at);

CREATE TABLE IF NOT EXISTS snapshots (
  id TEXT PRIMARY KEY,
  deal_id TEXT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
  side TEXT NOT NULL CHECK (side IN ('before','after')),
  entity TEXT NOT NULL,
  origin TEXT NOT NULL,
  file_name TEXT NOT NULL, sheet TEXT,
  headers_json TEXT NOT NULL,
  row_count INTEGER NOT NULL,
  mapping_json TEXT NOT NULL,
  mapping_meta_json TEXT NOT NULL,
  entity_confidence REAL,
  user_mapped INTEGER NOT NULL DEFAULT 0,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  content_hash TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_deal ON snapshots(deal_id);

CREATE TABLE IF NOT EXISTS source_rows (
  snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  row_number INTEGER NOT NULL,
  raw_json TEXT NOT NULL,
  PRIMARY KEY (snapshot_id, row_number)
);

CREATE TABLE IF NOT EXISTS audit_runs (
  id TEXT PRIMARY KEY,
  deal_id TEXT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
  started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL,
  engine_version TEXT NOT NULL,
  fingerprint TEXT,
  stages_json TEXT NOT NULL DEFAULT '[]',
  summary_json TEXT,
  error TEXT
);
CREATE INDEX IF NOT EXISTS runs_deal ON audit_runs(deal_id, started_at);

CREATE TABLE IF NOT EXISTS canonical_records (
  run_id TEXT NOT NULL REFERENCES audit_runs(id) ON DELETE CASCADE,
  side TEXT NOT NULL, entity TEXT NOT NULL, key TEXT NOT NULL,
  record_id TEXT, file TEXT NOT NULL, row_number INTEGER NOT NULL,
  raw_json TEXT NOT NULL, normalized_json TEXT NOT NULL,
  PRIMARY KEY (run_id, side, entity, key)
);

CREATE TABLE IF NOT EXISTS entity_matches (
  run_id TEXT NOT NULL REFERENCES audit_runs(id) ON DELETE CASCADE,
  entity TEXT NOT NULL, source_key TEXT NOT NULL, target_key TEXT,
  status TEXT NOT NULL, score INTEGER NOT NULL, reasons_json TEXT NOT NULL,
  PRIMARY KEY (run_id, entity, source_key)
);

CREATE TABLE IF NOT EXISTS match_overrides (
  id TEXT PRIMARY KEY,
  deal_id TEXT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
  entity TEXT NOT NULL, source_key TEXT NOT NULL, target_key TEXT NOT NULL,
  finding_id TEXT, reviewer TEXT, created_at TEXT NOT NULL, revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS findings (
  id TEXT PRIMARY KEY,
  deal_id TEXT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
  rule_id TEXT NOT NULL, severity TEXT NOT NULL, entity TEXT NOT NULL,
  title TEXT NOT NULL, subject TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  resolution TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  needs_rereview INTEGER NOT NULL DEFAULT 0,
  rent_risk_cents INTEGER NOT NULL DEFAULT 0, risk_key TEXT,
  deposit_cents INTEGER NOT NULL DEFAULT 0,
  pending_rent_cents INTEGER NOT NULL DEFAULT 0,
  confidence TEXT,
  evidence_hash TEXT NOT NULL, reviewed_hash TEXT,
  first_run_id TEXT NOT NULL, last_run_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS findings_deal ON findings(deal_id, active);

CREATE TABLE IF NOT EXISTS review_events (
  id TEXT PRIMARY KEY,
  finding_id TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
  deal_id TEXT NOT NULL,
  reviewer TEXT NOT NULL, action TEXT NOT NULL, note TEXT,
  from_status TEXT, to_status TEXT, payload_json TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reviews_finding ON review_events(finding_id, created_at);
CREATE INDEX IF NOT EXISTS reviews_deal ON review_events(deal_id, created_at);

CREATE TABLE IF NOT EXISTS intel_notes (
  id TEXT PRIMARY KEY,
  workspace TEXT NOT NULL,
  company_name TEXT NOT NULL, sector TEXT, location TEXT, event_date TEXT,
  website TEXT, source_link TEXT, notes TEXT, contact_role TEXT,
  pilot_status TEXT NOT NULL DEFAULT 'researching',
  linked_deal_id TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS intel_ws ON intel_notes(workspace, updated_at);

-- Integration planning (additive): plan -> dry runs -> sign-off -> release receipt.
CREATE TABLE IF NOT EXISTS integration_plans (
  id TEXT PRIMARY KEY,
  deal_id TEXT NOT NULL UNIQUE REFERENCES deals(id) ON DELETE CASCADE,
  template_id TEXT NOT NULL, template_version TEXT NOT NULL,
  template_json TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  contract_fp TEXT NOT NULL,
  tables_json TEXT NOT NULL,
  items_json TEXT NOT NULL,
  plan_hash TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS integration_events (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES integration_plans(id) ON DELETE CASCADE,
  deal_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  actor TEXT NOT NULL, action TEXT NOT NULL, item_id TEXT, note TEXT,
  before_json TEXT, after_json TEXT, plan_hash TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS integ_events_deal ON integration_events(deal_id, created_at);

CREATE TABLE IF NOT EXISTS dry_runs (
  id TEXT PRIMARY KEY,
  deal_id TEXT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
  plan_id TEXT NOT NULL, plan_version INTEGER NOT NULL, plan_hash TEXT NOT NULL, contract_fp TEXT NOT NULL,
  output_hash TEXT NOT NULL,
  summary_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS dry_runs_deal ON dry_runs(deal_id, created_at);

CREATE TABLE IF NOT EXISTS signoffs (
  id TEXT PRIMARY KEY,
  deal_id TEXT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
  plan_hash TEXT NOT NULL, dry_run_id TEXT NOT NULL,
  reviewer TEXT NOT NULL, statement TEXT NOT NULL,
  created_at TEXT NOT NULL, revoked_at TEXT, revoked_by TEXT
);
CREATE INDEX IF NOT EXISTS signoffs_deal ON signoffs(deal_id, created_at);

CREATE TABLE IF NOT EXISTS release_receipts (
  id TEXT PRIMARY KEY,
  deal_id TEXT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
  sha256 TEXT NOT NULL,
  receipt_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- Per-workspace secret for privacy-shield tokens. Never leaves this server.
CREATE TABLE IF NOT EXISTS pii_secrets (
  workspace TEXT PRIMARY KEY,
  secret TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


# Additive column migrations: (table, column, declaration). Never drops or rewrites existing data.
COLUMNS = [
    ("deals", "materiality_rent_cents", "INTEGER"),
    ("deals", "materiality_deposit_cents", "INTEGER"),
]


def init() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        for table, col, decl in COLUMNS:
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def dumps(v) -> str:
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False, default=str)


def loads(s: str | None, default=None):
    if s is None:
        return default
    return json.loads(s)
