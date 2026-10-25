# Test results

Results of the final validation run on 2026-10-25, all on synthetic data. Each line below was produced by the
command shown; nothing has been edited by hand.

## Summary

| Check | Command | Result |
|---|---|---|
| Backend tests | `cd backend && .venv/bin/python -m pytest -q` | **PASS** — 165 passed (90.6 s). Baseline was 123 |
| Lint | `ruff check app tests eval scripts --select E,F,W --ignore E501,E741` (backend) and `ruff check scripts` (repo) | **PASS** |
| Frontend types + build | `npx tsc -b --noEmit && npx vite build` | **PASS** |
| Engine ground-truth eval | `python -m eval.harness --seeds 30` | **PASS** — see §2 |
| Integration eval | `python -m eval.integrate_eval --seeds 30` | measured — see §3; two planted mistakes undetectable by design |
| Browser smoke test, desktop + mobile | `python scripts/smoke_test.py http://127.0.0.1:8765` | **PASS** |
| Browser E2E, Integrate journey | `python scripts/e2e_integrate.py http://127.0.0.1:8765` | **PASS** — 15 findings reviewed, receipt issued, no console errors, every step 390 px wide on a phone |
| Browser E2E, first-visit demo portfolio | `python scripts/e2e_demo_seed.py http://127.0.0.1:8765` | **PASS** — 3 seeded deals; every page and every Integrate step populated; no console errors |
| MCP stdio handshake | real `mcp` client over stdio | **PASS** — server `mergeproof 1.1.0`, 15 tools; tenant names returned as `PERSON_…`; bad deal ID gives an actionable `is_error` |

## 1. Backend tests by area

| File | What it pins down |
|---|---|
| `test_integrate.py` (16) | Template strictness; transform allowlist (no `eval`, no money constant); profile keys, references and PII masking; unit-key conflict routed to review; review guards and the event log; content-addressed plan hash; the demo (wrong key → 187/160/71 rejected, $387,115 and $319,975 → fix → clean, diff); sign-off → deterministic export → verify (observed-only $2,250 / $450 / $1,800) → receipt with tamper detection; edits void sign-off; blank deposit ≠ 0; formula guard; drift → rebase; derived template; workspace isolation; report sections; medium predicted findings block G7; a reference-backed key is offered when the audit mapping is wrong |
| `test_integrate_eval.py` (11) | Fast regression gate on a seed subset for every eval cell below |
| `test_deposit_blank.py` (2) | D-005 on both sides; zero is a real difference (D-002) |
| `test_demo_seed.py` (1) | Demo portfolio: three deals in their intended states (verifying / released with a verifying receipt / planning), reviews spread over real dates, seven fictional prospects on `.example` domains, seeded once per workspace, and "Try sample acquisition" still opens Bayview |
| `test_materiality.py` (1) | Flags only — the same rows before and after; thresholds editable; negatives rejected |
| `test_privacy.py` (4), `test_mcp.py` (9) | Token stability and scope; every tool's detailed output is free of sample names, emails and phones while IDs and cents survive; pagination; actionable errors; injection text returned as data with no state change |
| Earlier suites (121 tests) | Engine, matching, ingest, normalizers, review regressions, XLSX, AI, eval gate — all still green |

## 2. Engine evaluation (`eval/results.json`, 30 seeds)

| Measure | Rules only | With local AI |
|---|---|---|
| Anomalies detected by the right rule | 620/635 (97.6%) | same |
| Wrong claims | **0** | 0 |
| Routed to a person instead | 16 (was 33 before the corroboration fix) | 16 |
| Silent misses | **0** | 0 |
| Matching precision, every record type | 100% | 100% |
| Rent at risk and deposits exact | 30/30 | 30/30 |
| Rewritten work orders, right counterpart suggested | — | 44/44 |
| Messy headers mapped correctly | 15/29, 0 wrong | 24/29, 1 wrong (never auto-applied) |

## 3. Integration evaluation (`eval/results_integrate.json`, 30 seeds, Wilson 95% CIs)

| Cell | k/n | Rate [95% CI] |
|---|---|---|
| Clean migrations (tuning seeds): runs with a false claim | 0/30 | 0.000 [0.000, 0.114] |
| Clean migrations (holdout seeds): runs with a false claim | 0/30 | 0.000 [0.000, 0.114] |
| Anomaly recall, tuning seeds 1–30 | 620/635 | 0.976 [0.961, 0.986] |
| Anomaly recall, **holdout** seeds 1001–1030 | 617/631 | 0.978 [0.963, 0.987] |
| Rent at risk exact, holdout | 30/30 | 1.000 [0.886, 1.000] |
| Prompt-injection invariance (11,703 injected cells) | 30/30 | 1.000 [0.886, 1.000] |
| Offsetting rents caught while AGG-2 ties | 60/60 | 1.000 [0.940, 1.000] |
| Blank vs zero deposit semantics | 60/60 | 1.000 [0.940, 1.000] |
| Mapping: **unsafe auto-suggestions** | 0/1743 | 0.000 [0.000, 0.002] |
| Mapping: auto-suggestion precision | 1743/1743 | 1.000 [0.998, 1.000] |
| Mapping: auto coverage | 1743/1862 | 0.936 [0.924, 0.946] |
| Mapping: correct column ranked first | 1844/1862 | 0.990 [0.985, 0.994] |
| Mapping: correct column offered at all | 1850/1862 | 0.994 [0.989, 0.996] |
| Mapping: decoy columns never auto-suggested | 173/173 | 1.000 [0.978, 1.000] |
| Mapping: absent columns not auto-filled | 58/58 | 1.000 [0.938, 1.000] |
| Mapping after legacy columns confirmed in the Workspace: ranked first | 1862/1862 | 1.000 [0.998, 1.000] |
| Mapping with local AI: unsafe auto-suggestions | 0/1743 | 0.000 [0.000, 0.002] |
| Dry run: correct plan comes out clean | 10/10 | 1.000 [0.723, 1.000] |
| Dry run: planted mapping mistake blocks sign-off | 120/140 | 0.857 [0.790, 0.906] |
| Export refused when unsigned, stale or drifted | 8/8 | — |

**Planted mistakes, 10 runs each.**
- **Blocked 10/10:**
  - unit key from the door code
  - base rent from market rent
  - lease status mapped wrongly (both directions)
  - move-in date from the signed date, from the end date, or written DD/MM/YYYY
  - resident reference from the unit column
  - work-order status open → Closed
  - deposit from the required-deposit column
  - unit number from the door code
  - email from the phone column (refused at edit by the type guard)
- **Blocked 0/10:** property name from the street address, and rent frequency constant "Weekly". Neither leaves a
  contradiction in the data or the rules, so they stay a reviewer's job.

**What the evaluation changed** (each change has a regression test):
1. **Name similarity tolerates typos.** A transposed surname used to produce a false L-008 on a clean migration.
2. **Tenant corroboration no longer skips its own review candidate.** This removed 37 needless review items.
3. **The dry run refuses an incompletely mapped legacy export.** Before, a correct plan predicted 708 bogus
   findings.
4. **G7 also blocks predicted *medium* findings, and G6 includes per-property tie-outs.** Date mistakes used to
   pass sign-off. Blocking recall went from 90/140 to 120/140.
5. **The suggester offers reference-backed key columns the audit mapping missed.** They are always routed to
   review. Top-1 went from 1823 to 1844, and unsafe auto-suggestions stayed at 0.

## 4. What was not tested

- **Real vendor importers.** The dry run models a strict importer.
- **Real tenant data.** Every run here used synthetic data.
- **Multi-user concurrency and authentication.** These aren't implemented: workspaces are per browser.
- **Cross-browser coverage.** Chromium only.
