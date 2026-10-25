# Integration blueprint — Plan → Dry run → Verify

This document covers how MergeProof plans, rehearses and verifies a migration. It is written for engineers and
reviewers checking the design. Code lives in `backend/app/integrate/`; tests are in
`backend/tests/test_integrate.py`.

## 1. The problem it closes

The audit answers *"what survived the migration?"* only after the load. That is too late for the cheapest class of
mistakes, the ones made in the mapping itself. A wrong key column, a rent field taken from the wrong place or an
unmapped status value can be caught before anything is imported. Integrate adds three things:

- a reviewed, hashed **plan**
- a **dry run** that predicts the audit's findings
- a **verify loop** that separates plan mistakes (predictable) from load mistakes (only observable afterwards)

## 2. Data flow

```
legacy exports (before snapshots)
   │  profile.build ─ types · blanks · uniqueness · shapes · key candidates · value-inclusion refs
   ▼
source contract  (fingerprint = sha256 of entity, file, headers, row count, content hash)
   │
target template ─ cedar_import_v1.json (bundled, fictional)  |  derived from consolidated exports
   │  mapping.suggest ─ evidence per column → candidates → status
   ▼
integration plan ─ items {sources, transform, status, decision}, version, plan_hash   ◀── reviewer actions (logged)
   │  dryrun.execute ─ allowlisted transforms, strict-importer checks, rejection cascade, lineage
   ▼
simulated import tables ──► preflight + blast radius
   │                   └──► runner.analyze(legacy, simulated) ─► predicted findings + tie-outs (same rules)
   ▼
readiness G1–G7 ─► sign-off (plan_hash + dry_run_id) ─► G8 ─► export.zip (deterministic; must match the dry run)
                                                             │
                          person loads the files ◀───────────┘   (MergeProof writes to no vendor system)
                                                             │
actual target export (after snapshots) ─► runner.run_audit ─► findings
   ▼
readiness.compare ─ predicted vs observed by anchor · three-way tie-out ─► G9–G11 ─► release receipt (sha256)
```

## 3. Modules

| Module | Responsibility | Writes |
|---|---|---|
| `template.py` | Strict validation of template JSON (types, refs only to earlier tables, enum vocabularies, date formats, key prefixes); deriving a template from the consolidated exports | — |
| `profile.py` | Per-column profile and cross-file value inclusion; contract fingerprint. Cached on (fingerprint, mappings) | — |
| `mapping.py` | Evidence scoring, type guards, conflict detection, bulk eligibility, note requirements | — |
| `transforms.py` | The allowlist and per-value application. No expressions, no code, no model output | — |
| `plan.py` | Plan create/rebuild, item actions, hashing, versioning, drift, rebase, dependency warnings, event log | `integration_plans`, `integration_events` |
| `dryrun.py` | Executor with lineage and rejection cascade, preflight, blast radius, simulated verify via `runner.analyze`, tie-outs, rerun diff | `dry_runs` |
| `readiness.py` | Gates G1–G11, sign-off and revoke, prediction-vs-observation, release receipt | `signoffs`, `release_receipts` |
| `export.py` | Blueprint JSON, deterministic zip, manifest, formula guard | event only |
| `api.py` | Workspace-scoped routes under `/api/deals/{id}/integrate` | — |

Storage is additive only: six new tables and two nullable `deals` columns (`materiality_*`). They are added by
`db.init()`, which checks `PRAGMA table_info` before any `ALTER TABLE … ADD COLUMN`. Nothing existing is dropped or
rewritten.

`engine/runner.py` was refactored minimally. A pure `analyze(inputs, labels, as_of, overrides, ai)` now returns the
engine pass without persisting. `run_audit` calls it unchanged, and the dry run calls it with simulated after-side
inputs. Because of that, a predicted finding and its post-import counterpart share rule ID and anchor, e.g.
`L-001|BV-L-1042`.

## 4. Mapping evidence

For each target column, candidates are scored from additive, named signals:

| Signal | Points | Meaning |
|---|---|---|
| `audit` | +0.5 (+0.6 derived) | The audit's own mapping binds this canonical field to the column. A reviewer may already have confirmed it in the Workspace. Derived cases are `legacy_ref` ← the entity's ID, and `full_name` ← first + last name. |
| `references` | +0.3 × inclusion | For a key: the share of other files' reference values found in this column. For a ref: the share of this column's values found in the referenced table's key. |
| `name` | +0.3 exact, ≤ +0.15 similar | The header matches the template's column name. |
| `ai` | ≤ +0.2 | Local embedding similarity. Used **only** when no structural candidate exists, and labelled as AI. |
| `template` | +0.4 | A template-provided constant for a column with no legacy equivalent, e.g. `rent_frequency = Monthly`. |

Type guards remove candidates that can't hold the value. A key must be unique, complete and space-free; money must
parse; dates must parse; a ref can't be a personal-data column. Removed candidates are shown under "ruled out" with
the reason.

**Status rules.**
- `needs_review` (contested) when:
  - an exact name match disagrees with the top candidate, or
  - fewer than 98% of references resolve through the top candidate, or
  - the runner-up is within 0.15, or
  - legacy code-list values have no target equivalent.
- `suggested` (exact) when the audit mapping supports the column and nothing contradicts it.
- `needs_review` (weak) for name or AI support only.

Approving a contested or weak column, or choosing something other than the top candidate, needs a written note.
Bulk approval covers `suggested` + `exact` + non-financial columns only. Money columns and anything the template
flags `financial` (lease status) are approved one at a time.

## 5. Transform allowlist

| Op | Target types | Notes |
|---|---|---|
| `copy` | text, name, key, unit, email, phone | trimmed text |
| `rekey` | key | `prefix` + zero-padded sequence in source row order; crosswalk exported |
| `ref_lookup` | ref | looks the source value up in the referenced table's crosswalk; the table is fixed by the template |
| `date_format` | date | parse, then `MM/DD/YYYY`, `YYYY-MM-DD` or `DD/MM/YYYY` |
| `money` | money | exact integer cents re-formatted as `1234.56`. Never scaled, summed, defaulted, or a constant |
| `enum_map` | enum | canonical vocabulary → target values (target values validated against the template) |
| `concat` | text, name | 2–4 columns, fixed separators |
| `constant` | text, enum | only for columns with **no** canonical field; must be one of the template's values |
| `unit_label`, `phone`, `email`, `int` | matching types | the same normalizers the audit uses |

Blank in always gives blank out. A blank deposit balance is never written as `0.00`: it fails `PF-DEP`, and in the
audit it is **D-005**.

## 6. Strict-importer model and blast radius

| Check | Blocking |
|---|---|
| `PF-MAP` required column has no source | yes |
| `PF-REQ` required value blank | yes |
| `PF-DEP` deposit balance blank (unknown ≠ 0) | yes |
| `PF-TYPE` unreadable value (required or financial column) | yes |
| `PF-ENUM` code-list value without target equivalent | yes |
| `PF-LEN` longer than `max_length` | yes |
| `PF-KEY` duplicate key | yes |
| `PF-REF` reference doesn't resolve | yes |
| `PF-CASCADE` references a row that was itself rejected | yes |
| `PF-WARN` optional value unreadable → imported blank (reported, counted) | no |

Rejected rows are removed from the simulated import. A rejected row's key leaves the crosswalk, so dependants fail
with `PF-CASCADE` and inherit the root cause. **Blast radius** groups every rejected row by root cause and prices
it from the legacy canonical records: active leases and their monthly rent, deposit dollars, open work orders. The
model is conservative and stated on every report. MergeProof does not know the destination's real importer.

## 7. Hashes and invalidation

| Artifact | Hash basis | Invalidated by |
|---|---|---|
| Source contract | entity, file, sheet, headers, row count, content hash of every legacy snapshot | any legacy upload or removal |
| Plan hash | template id/version, contract, table bindings, every item's sources + transform + ignored flag | any mapping change (not approvals) |
| Dry run | plan hash + contract at run time; `output_hash` = sha256 over the simulated CSVs | the plan hash or contract moving on |
| Sign-off | plan hash + dry-run id | either changing; superseded on re-sign |
| Export zip | rebuilt from the plan; refused unless the rebuild equals the dry run's `output_hash` | — |
| Receipt | sha256 over canonical JSON of plan, dry run, sign-off, manifest hash, verification run, gates, tie-outs, limits | stored copy edited (`verifies: false`) |

## 8. Gates

| Gate | Phase | Pass condition |
|---|---|---|
| G1 | plan | contract fingerprint unchanged since build or rebase |
| G2 | plan | every item `approved` or `ignored` (ignored needs a reason and only applies to optional columns) |
| G3 | plan | no financial item pending |
| G4 | simulate | latest dry run's plan hash and contract equal the current ones |
| G5 | simulate | 0 rejected rows |
| G6 | simulate | AGG-1…AGG-5 (active leases, active rent, matched rent, deposits, open work orders) pass, and every property's active rent ties |
| G7 | simulate | 0 predicted critical, high or medium findings (low/informational allowed) |
| G8 | approve | live sign-off on this plan hash and dry run |
| G9 | verify | a completed audit started after the sign-off, on current data |
| G10 | verify | no open critical/high findings |
| G11 | verify | no open finding with rent or deposit impact |

Sign-off needs G1–G7. Export needs G1–G8. The receipt needs all eleven.

## 9. Privacy, agents and limits

- **MCP outputs** pass through `app/privacy.py`. Tenant names, emails and phones become HMAC tokens with a
  per-workspace secret; IDs, rows and cents survive. Agents can propose a plan and run a dry run. They can't
  approve, sign, export, issue receipts or resolve.
- **Workspace IDs** separate demo visitors. They are not authentication. Don't use real tenant data on an open
  deployment.
- **Single reviewer.** Sign-off is single-reviewer. Two-person approval, identity and an immutable audit store are
  the next steps for production use.
- **The bundled Cedar template is fictional.** Real destinations would ship their own template or derive one from
  an export.
