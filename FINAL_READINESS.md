# MergeProof — final readiness

This is the status checklist for the Plan → Dry run → Verify upgrade. Every PASS below was produced by a command
that was actually run; [TEST_RESULTS.md](TEST_RESULTS.md) has the outputs. All data is synthetic.

## Phase 0 — baseline (before any change)

Baseline commit `92fec4e`: clean worktree, no `CLAUDE.md` / `AGENTS.md`, and the secret scan of tracked files
found nothing.

| Check | Result |
|---|---|
| Backend tests | 123 passed |
| Lint (ruff E,F,W) | pass |
| Frontend types + build | pass |
| Ground-truth eval (30 seeds) | 635 injected, recall 0.9764, 0 wrong claims, 33 routed to review, money exact 30/30 |
| Browser smoke (desktop + mobile) | pass |

## Final state

| Check | Result |
|---|---|
| Backend tests | **164 passed** |
| Lint | pass |
| Frontend types + build | pass |
| Engine eval | 620/635 detected, 0 wrong claims, **16** routed to review, 0 silent misses, money exact 30/30 |
| Integration eval | 0/1743 unsafe auto-suggestions; holdout recall 0.978 [0.963, 0.987]; dry-run blocking recall 120/140 |
| Browser smoke + Integrate E2E | pass (no console errors; phone width 390 px on every Integrate step) |
| MCP stdio handshake | pass (15 tools, PII tokenized) |

## Phase status

| Phase | Status | Where |
|---|---|---|
| 0 Baseline | DONE | above |
| 1 Plan: profiling, source contract, mapping studio, versioning, drift | DONE | `app/integrate/{profile,template,mapping,plan}.py`, Integrate §01–03 |
| 2 Dry run: allowlist transforms, lineage, preflight, blast radius, simulated verify, export, rerun diff | DONE | `dryrun.py`, `transforms.py`, `export.py`, Integrate §04–05 |
| 3 Controls + verify loop: tie-outs legacy → simulated → actual, gates, sign-off, prediction vs observation, receipt, reports, materiality, D-005 | DONE | `readiness.py`, `reports.py`, Integrate §06, Proof §12 |
| 4A Privacy shield | DONE | `app/privacy.py`, MCP outputs |
| 4B MCP tool quality | DONE | `app/mcp_server.py` v1.1.0 (15 tools) |
| 4C Evaluation rigor | DONE | `eval/integrate_eval.py`, `results_integrate.json` |
| 4D Contextual hybrid matching | NOT DONE (optional) | Skipped on purpose. The engine already reaches 0 wrong claims; the measured gap is the mapping planner, which was improved instead |
| 4E Agent Skill | DONE | `skills/migration-continuity-audit/SKILL.md` |
| 4F MCP App (`ui://`) | NOT DONE (stretch) | Not started |
| 5 Product experience | DONE | Integrate route + stepper, Command Center readiness/continuity track, Findings material filter, nav renumbered |
| 6 Validation | DONE | TEST_RESULTS.md |
| 7 Artifacts | DONE | this file, INTEGRATION_BLUEPRINT.md, DEMO_SCRIPT.md, TEST_RESULTS.md, README §Integrate |

## Decisions made without asking (routine; each is reversible)

- **The import template is fictional and bundled.** It is `cedar_import_v1.json`; templates can also be derived
  from the consolidated exports. Arbitrary template upload was not added.
- **Strict-importer model.** Rejected rows cascade to their dependants. The model is stated on every report.
- **The plan hash covers mapping content only, not approvals.** Approving a column doesn't void a dry run;
  changing a mapping does.
- **G7 blocks predicted *medium* findings too, and G6 includes per-property tie-outs.** The eval showed that date
  mistakes producing ~175 medium findings would otherwise pass sign-off.
- **Verification (G9) must post-date the sign-off.**
- **Sample materiality: rent ≥ $500/mo, deposit ≥ $1,000.** Flags only.
- **`propose_integration_plan` was added as an MCP tool.** It creates suggestions only and never replaces an
  existing plan.

## Security and process notes

- **Credentials.** Earlier prompts in this project's history contained what looked like credentials. Treat them as
  compromised and **rotate them**. They were not used, printed, committed, or put into fixtures or test output. No
  `.env` entries were created, and no third-party account was authenticated.
- **Data.** All demo data is synthetic. Browser workspace IDs are *not* tenant authorization. Don't load real
  tenant data into an open or unauthenticated deployment.
- **Agents.** No model invents records, calculates money, certifies, resolves exceptions, or writes to a vendor
  system. MCP has no approve, sign-off, export, receipt or resolve tool. Agent outputs are PII-tokenized.
- **Storage.** Migrations are additive only: six new tables and two nullable columns. Nothing was dropped or reset.
- **Git.** Changes are committed locally as `0xpinara`, without AI attribution. **Nothing was pushed**; pushing
  waits for your confirmation.
- **VibeCon.** This work was built with Claude Code and is not an Emergent submission.

## Known limitations

- **Single-reviewer sign-off.** There are no accounts, identity, two-person approval or immutable audit store.
- **Undetectable mistakes.** The dry run can't detect a mapping mistake that leaves no contradiction (e.g.
  property name ← street address, rent frequency "Weekly"). A reviewer is still needed for those.
- **Single source file.** One legacy file per entity feeds the plan; extra files are listed as "not used".
- **Locale coverage.** The privacy shield's phone catch-all targets North American formats plus `+` numbers.
  Names that are also ordinary words elsewhere in the deal are replaced only as full names.
