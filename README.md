# MergeProof — *Every record accounted for.*

MergeProof is the verification layer for business integrations. After one property manager acquires another and
moves its records into a consolidated system, MergeProof compares the **before** (legacy) and **after**
(consolidated) exports and proves which tenants, leases, deposits and work orders survived the move. It also shows
exactly what went missing or changed, with the source file and row behind every claim.

It is not an ETL tool, CRM or chatbot. The engine is deterministic and rule-based, and every number on screen is
computed from stored records. A small open-source AI model runs locally to *suggest* what the rules can't settle.
A person accepts every suggestion, and a ground-truth evaluation measures both the rules and the AI.

---

## Run it

```bash
./run.sh            # creates backend/.venv, installs deps, fetches the local AI model, builds the UI, serves :8000
./run.sh test       # backend test suite (123 tests)
./run.sh eval       # ground-truth evaluation over 30 randomized migrations -> backend/eval/results.json
./run.sh mcp        # MCP server on stdio, so AI agents can run audits and read evidence
MERGEPROOF_AI=off ./run.sh   # rules only, no model download
```

Requirements: Python 3.12 (`PYTHON=python3.x ./run.sh` to choose another) and Node 18+.

Development with hot reload (two terminals):

```bash
cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev          # http://localhost:5173, proxies /api to :8000
```

State lives in SQLite at `backend/data/mergeproof.db` (override with `MERGEPROOF_DB=/path/file.db`). It survives
refreshes and restarts. Delete the file to reset.

Browser smoke test (desktop + mobile, takes screenshots, exits non-zero on failure). It works against localhost or
a public URL:

```bash
pip install playwright && python -m playwright install chromium
python scripts/smoke_test.py http://127.0.0.1:8000
```

---

## The 90-second demo (also the acceptance path)

1. **Command Center → "Try sample acquisition".** This loads the synthetic *Cedar × Bayview | Tenant & Rent Roll
   Migration* deal with 12 exports: 6 legacy, 6 consolidated, each side with its own column names, ID schemes and
   formats.
2. **Audit Workspace → "Run continuity audit".** The terminal plays back the five real stages (Preparing exports →
   Mapping fields → Matching entities → Testing invariants → Building evidence) with their actual counts and timings.
3. **Command Center** shows a continuity score of **98.9%** (173/175 active leases), 3 critical findings,
   **$4,425/mo estimated rent at risk** and **$4,140 in deposit discrepancies** tracked separately. It also shows the
   blind spot: *175 = 175 active leases and only −$205/mo at portfolio level, yet three leases are at risk.* The
   ASCII skyline is the real unit map, and clicking a red window opens its evidence.
4. **Findings → "Missing lease" (Dana Whitfield, Unit 4B).** The legacy row is `bayview_leases.csv:r43`, with no
   match in Cedar's import and the search explained. The tenant record exists in Cedar with no lease attached.
5. **"Rent changed (lower)" (Theo Alvarez, 5A):** $2,500.00 → $2,050.00, a −$450.00/mo receipt, and the IDs marked
   *re-keyed* rather than flagged.
6. **"Deposit balance missing" (Mina Okafor):** $1,800, labelled a tenant liability and never added to rent.
7. **Mark expected exception** on the missing lease with a reason, then refresh. The status, note, reviewer and
   timestamp persist, and the KPIs recompute ($4,425 → $2,175 open).
8. **Continuity Proof → Download PDF / Findings CSV.** Both include the current review state and notes.
9. **AI, suggestion only:** the "Mold spotting on bathroom ceiling" work order was re-entered at property level
   with a retyped description, so the rules alone call it missing. The local model suggests the counterpart
   (similarity 0.60, same property, 4 days apart), labelled as AI, and you confirm it. Money totals don't move.
   To see column suggestions, upload `cedar_rent_roll_messy_headers.csv` from the Workspace's sample downloads.
10. Optional: **Deal Intelligence → Add prospective integration**, a manual record with a source link.
   *Research in PitchBook* is only an external link.

Failure handling: an upload whose first row is data is rejected with a clear message. Binary files, empty files,
header-only files, oversized files, files over the row limit, `.xls`, and other types are also rejected cleanly.
Rerunning the audit never clones findings.

---

## Seeded anomalies in the sample (all fictional)

| Case | Where | What the engine reports |
|---|---|---|
| Active lease missing ($2,250) | Juniper Terrace 4B | L-001 critical, full rent at risk; its $2,250 deposit is D-001 (separate) |
| Rent $2,500 → $2,050 | Quarry Row Lofts 5A | L-002 critical, −$450/mo |
| Deposit absent ($1,800) | Tidewater House 3A | D-001, liability only |
| Deposit $1,650 → $1,560 | Wren Street Six 1C | D-002, $90 |
| Tenant duplicated under two new IDs | Sorrel Court 1B | T-001; the lease still matches via either record |
| Open urgent work order missing ("No heat") | Tidewater House 3C | M-001 critical |
| Lease points at a unit ref that doesn't exist | Sorrel Court 2A | L-009 |
| Lease attached to the wrong unit (1C → 1D) | Halyard Gardens | L-007 |
| Lease marked "Past" **and** rent cut $1,725 → $1,575 | Halyard Gardens 3D | L-004 + L-002, counted **once** ($1,725) |
| Rent increase $1,950 → $2,095 | Wren Street Six 2B | L-003, review only, not risk |
| End date 2027-03-31 → 2026-03-31 | Wren Street Six 3A | L-005 |
| Open faucet leak closed during migration | Wren Street Six 2A | M-002 |
| Open mold ticket re-entered at property level, description retyped | Sorrel Court 4C | M-001; with AI on it becomes *needs review* with one labelled AI suggestion |
| Ambiguous: two "Alex Kim" candidates (52 vs 53/100) | Larkspur Commons 2C | L-006 needs review, $1,875 shown as *pending*, never "missing" |
| Benign: all IDs re-keyed; "Avenue" → "Ave."; "Apt 4B" → "#4-B"; date/money/phone/status formats | everywhere | no findings; counted under *Recognized as harmless* |
| New post-cutover lease; expired history; completed work orders not carried | various | low/informational |

`backend/scripts/generate_sample.py` regenerates the CSVs byte-for-byte (seeded). `tests/test_sample_fixture.py`
asserts each case, the exact finding set, the totals and determinism.

---

## How the engine decides

**Normalize, keep raw.** Dates (ISO, US slash, month names, Excel serials) are converted to ISO. Money goes through
`Decimal` to integer cents. Emails are lowercased, phones reduced to digits, address abbreviations unified, and unit
labels canonicalised (`Apt 4B`, `#4-B`, `Unit 04B` → `4B`). Status and priority vocabularies are mapped. Raw values
are kept for evidence.

**Match with named signals, one-to-one.** Every candidate pair gets an additive score with readable reasons. Leases,
for example: same unit +40, same tenant +35 (or +28 when tenant identity is only probable), same start +15, same
end +5, same rent +5. A legacy reference equal to the legacy ID scores 100. Shared IDs count only when the two
systems' ID spaces actually overlap. Thresholds per entity are confirm / probable / review: leases 80/60/35, tenants
70/50/30.

- Assignment is greedy by score but strictly one-to-one. A pair below *confirm* that has a rival within 10 points
  goes to **human review**; it is never auto-accepted. Two legacy records competing for the same consolidated
  record within 10 points go to review at any score.
- Tenant name alone scores 20, below the review threshold, so two "Alex Kim" records never collapse.
- If two consolidated records both strongly match one legacy record, that is reported as a duplicate.
- A reviewer's confirmed match is stored as an override and applied on the next run, where downstream rent, deposit
  and unit checks then use it.
- Lease scores are expressed against the evidence the exports can actually support. Two plain rent rolls with no
  tenant or date columns aren't penalised for columns that don't exist, and the match explanation says so.
- With no units or tenants files (two plain rent rolls), the engine falls back to the leases' own unit and tenant
  reference text. If a unit label like `1A` appears in several buildings and the property IDs were re-keyed, the
  label alone isn't trusted, and those leases go to review.
- A field exported on only one side is shown as *not in export*, never as a change.
- A lease export with no status column is treated as current leases. Notice and eviction statuses count as active.
  A status the engine can't interpret is never assumed ended: the lease goes to review, with its rent shown as
  pending rather than at risk.

**Money semantics.**
- *Estimated monthly rent at risk (requires review):* each legacy lease contributes at most once, using the largest
  open exposure among its findings. A missing or inactivated lease contributes its full rent; a lower rent
  contributes the difference. Rent increases and leases under review are excluded, and closed findings drop out.
- *Deposit discrepancies* are liabilities and are reported separately.
- *Continuity score* = active legacy leases with a confident counterpart (or closed by a reviewer) ÷ all active
  legacy leases. It measures reconciliation completeness, not legal compliance.

**Stable findings.** IDs are `hash(deal, rule, anchor record)`, so reruns upsert rather than duplicate. Review
decisions survive reruns. If a reviewed finding's evidence changes, it is flagged *needs re-review* and a system
event is logged. Findings that stop appearing become *no longer detected* but keep their history.

---

## AI, used carefully

| | |
|---|---|
| **Model** | [`minishlab/potion-retrieval-32M`](https://huggingface.co/minishlab/potion-retrieval-32M) static embeddings via `model2vec` (MIT). About 130 MB, CPU only, numpy (no PyTorch). Downloaded once, then runs offline on the MergeProof server. No API, no cost, no data leaves the machine. |
| **Suggests** | (1) a consolidated work order that may be a legacy request with its description rewritten, *only* when the rules find no candidate, restricted to unclaimed records in the same property within 30 days; (2) a column for fields the alias rules couldn't map. |
| **Never** | pairs records, maps columns or changes amounts on its own; contributes to rent at risk, deposits or the continuity score; makes a decision. Each run records `decisions_by_ai: 0`. |
| **Shows** | an `AI` badge, the similarity, the structural reasons, and "adds no match points — you decide". Column suggestions need an explicit **Apply**. |
| **Discloses** | the run summary, the terminal, the Command Center, the Proof page and the PDF all state the model and how many suggestions it made. |
| **Degrades** | `MERGEPROOF_AI=off`, a missing package or a failed download all mean rules only, with the reason shown. |

Why it's this narrow: we compared four free local models (two static, two ONNX transformers). None reliably tells
"kitchen tap dripping" (the same issue) from "leak under bathroom sink" (a different one) on wording alone. So
structure (property, dates, unclaimed records) filters first, the model only ranks, and a person decides.

## Evaluation — how we know it works

`backend/eval/harness.py` turns the legacy sample into randomized consolidated exports with known answers:

- **Reformatting:** every ID re-keyed, rows shuffled, headers renamed, and dates, money, phones, emails, unit labels
  and status vocabularies reformatted.
- **Messy contacts:** about 16% of tenants lose their email and/or phone, and about 5% get a typo in their surname.
- **Injected anomalies:** 13 types at random positions.
- **Rewritten work orders:** these test the AI.

Latest run (`./run.sh eval`, 30 seeds):

| Measure | Rules only | With local AI |
|---|---|---|
| Anomalies detected by the right rule | 97.6% (620 / 635) | same |
| Wrong claims (findings nobody injected) | **0** | 0 |
| Uncertain cases routed to a person instead | 33 | 33 |
| Anomalies missed silently | **0** | 0 |
| Matching precision, every record type | **100%** | 100% |
| Rent at risk and deposits exact to the cent | 30 / 30 | 30 / 30 |
| Rewritten work orders: right counterpart suggested | — (reported missing) | **44 / 44** |
| Truly missing work orders that still got a suggestion | — | 1 / 45 |
| Messy column names mapped correctly (29) | 15, 0 wrong | 24, 1 wrong (never auto-applied) |

`tests/test_eval.py` runs the harness on every test run as a regression gate. It fails on any wrong claim, any
silent miss, any wrong pairing, or money that isn't exact. The evaluation found two real weaknesses, both now
fixed: "wrong tenant" claims when contact details were blank, and tenants whose identity is only corroborated by
the lease they hold. These are synthetic results: they show the engine's behavior, not industry loss rates.

## For AI agents (MCP)

`app/mcp_server.py` exposes MergeProof over the Model Context Protocol (stdio, MCP SDK 2.x). Its tools are
`list_audits`, `load_sample_acquisition`, `run_audit`, `get_summary`, `list_findings`, `get_evidence` and
`ai_status`. They are annotated read-only or idempotent, and none is destructive. There is deliberately no tool
that resolves, excepts or confirms a finding: agents can investigate and explain, and a named person decides in
the app.

```json
{"mcpServers": {"mergeproof": {"command": "/path/to/backend/.venv/bin/python", "args": ["-m", "app.mcp_server"],
  "cwd": "/path/to/backend", "env": {"MERGEPROOF_WORKSPACE": "ws_my_agent_01"}}}}
```

---

## What's implemented vs. not

**Implemented:**
- Sample deal, CSV and XLSX upload (multi-file, multi-sheet), auto-mapping with confidence, and mapping correction
  with re-validation.
- The deterministic engine: 34 record-level rules and 8 portfolio totals.
- Findings inbox with severity, record type, status, money, rule and keyword filters.
- Evidence drawer with side-by-side rows, file:row provenance, a receipt-style calculation, match reasoning, and
  ambiguous candidates you can confirm.
- Review workflow: investigate, confirm match, expected exception (reason required), resolve, reopen, comment. Every
  action keeps an append-only log.
- Continuity Proof page, PDF (ReportLab), findings CSV (formula-injection safe) and print stylesheet.
- Unit-map skyline, Deal Intelligence CRUD, About/pitch page.
- Local AI suggestions for work orders and columns, with labels and an off switch.
- Ground-truth evaluation harness used as a regression gate.
- MCP server for AI agents (read and rerun only).
- Per-browser demo workspaces, reduced-motion support, keyboard and focus handling, mobile layout.

**Not implemented (deliberately):**
- Vendor connectors (AppFolio, Buildium, Yardi).
- Accounts, SSO, real tenant separation or permissions. Browser workspaces are **not** access control.
- Billing.
- PitchBook or WRDS data integration (only a plain external link).

**Privacy and security minimums in place:** parameterized SQL; size, row, column, file-count and XLSX
decompression limits; sanitized file names; restricted file types; no evaluation of cell content; errors logged
without row data; security headers; a required synthetic-data confirmation before uploads. Parsed rows are stored
for evidence and original files are not kept. Deleting an audit removes its data. Real customer data would need
access controls, tenant separation, a retention policy and a security review first.

---

## Layout

```
backend/app/
  normalize.py   ingest.py   schema.py        values, parsing, column aliases
  ai/            embeddings.py (local model, lazy, optional) · assist.py (suggestion-only logic)
  mcp_server.py  MCP tools for AI agents
  engine/        canonical → matching → rules → runner, metrics (live KPIs)
  main.py        API + static frontend        reports.py  PDF / CSV / report JSON
  sample_data/   synthetic Cedar × Bayview exports
backend/tests/   fixture, matching, ingest, normalizer and API tests
frontend/src/    React + TypeScript; hand-built design system in styles.css
  art/dusk.ts    the hero's animated ASCII scene (halftone dots, ordered dither, palette-quantized; canvas)
backend/eval/    ground-truth evaluation harness + latest results.json
scripts/         browser smoke test
```
