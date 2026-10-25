# MergeProof demo script (≈ 6 minutes)

This is the click-path for presenting MergeProof live. Everything shown is synthetic: Cedar Property Management is
acquiring Bayview Rentals.

**Setup:**
- `./run.sh`, then open http://localhost:8000
- Optional: run `MERGEPROOF_AI=off ./run.sh` to show rules only.
- Set the reviewer name in the top bar (e.g. "Ana"). It is stamped on every decision.

---

## 1 · The problem (30 s)

**Command Center → Try sample acquisition.** You land in the Audit Workspace with 12 exports: 6 legacy and 6
consolidated. Each side has its own headers, ID schemes and formats.

> "Cedar is about to move 187 leases out of Bayview's system. A migration tool will move the rows. Nobody proves
> the rent, deposits and work orders survived."

## 2 · Profile (30 s)

**Integrate (§03) → 01 Profile.**
- Show the source contract fingerprint.
- Show the *Who points at what* panel. `bayview_units.csv` has two unique columns:
  - `unit_id` is referenced by 100% of lease and work-order unit values.
  - `unit_code`, a door code like `MF-1A`, is referenced by **nothing**.
- Point out that personal-data columns show *hidden* instead of examples.

## 3 · Map (40 s)

**02 Map → Propose a mapping plan** (template: *Cedar consolidated platform — import template*, fictional).
- 32 target columns, each with a legacy source, a transform in plain English and its evidence.
- Two are contested:
  - `units.unit_code`
  - `leases.rent_frequency` (Bayview has no such column; the template suggests "Monthly")
- Point at the `$` marks: financial columns are approved one by one, never in bulk.

## 4 · Review: make the tempting mistake (60 s)

**03 Review → Approve 26 as Ana.** Bulk approval covers the exact, non-financial columns only.

Open **units.unit_code → Decide**.
- Candidates: `unit_id` scores 91 (audit map +50, references +30, similar name +11). `unit_code` scores 30 (exact
  name +30, references +0).
- Reason shown: *Name match `unit_code` disagrees with the stronger evidence… 100% of references resolve through
  `unit_id`, 0% through `unit_code`.*
- Type the note *"Template calls the key unit_code."* and click **Use this** on `unit_code`. A note is required
  because this goes against the evidence.

> "This is what a rushed implementer does: the names match."

Approve the rest:
- `leases.rent_frequency` (with a note)
- the three financial columns, one at a time

## 5 · Simulate: watch it break (60 s)

**04 Simulate → Run dry run.** The hazard banner reads **SIMULATED OUTPUT — NOT IMPORTED, NOT VERIFIED**.

- **Rejected: 418 rows.**
  - Blast radius: `PF-REF leases.unit_code` takes down **187 leases**. That is **175 active leases with $387,115/mo
    in rent** that would not bill.
  - By cascade, **160 deposits** go with it: **$319,975** of liability left unrecorded.
  - Separately, **71 work orders** fail, including 32 open ones.
- Tie-outs are red for every property: e.g. *Marigold Flats $67,250 ≠ $0*.
- 380 predicted findings, starting with L-001 *Missing lease*. These are the **same rules** the post-import audit
  uses.
- Simulated rows: the leases table shows only rejects, each with its root cause.

## 6 · Fix it and re-run (40 s)

**02 Map → units.unit_code → Evidence.**
- Note: *"Leases and work orders reference unit_id; unit_code is the door code."*
- Click **Use this** on `unit_id`.

**04 Simulate → Run dry run again.**
- 799/799 rows simulated, **0 rejected, 0 predicted findings**.
- Every tie-out passes to the cent: *$387,115 ═ $387,115*, per property too.
- The diff line reads: *rejected 418 → 0 · predicted findings −380 · rent at risk $387,115 → $0*.
- Hover a simulated cell to see its lineage: legacy file, row, column and transform.

## 7 · Sign off & export (40 s)

**05 Export.**
- G1–G7 pass. Read the acknowledgement aloud: *single-reviewer, not an audit opinion*. Tick it, then **Sign off as
  Ana**.
- **Download import package.** It contains:
  - import CSVs in load order
  - legacy → new key crosswalks
  - `lineage.csv`
  - `plan.json`
  - a sha256 manifest

> "MergeProof never writes to Cedar. A person loads these files."

## 8 · Verify: what the plan couldn't predict (60 s)

**06 Verify → Run post-import verification.** This runs on the consolidated export taken *after* the load.

- Predicted **0**, observed **19**. All of them are **import-time defects**. The plan was clean, so the load
  introduced them.
- Three-way tie-out: legacy $387,115 ═ simulated $387,115 ≠ actual **$386,910 (−$205)**.

> "Equal-looking totals hide offsetting errors. The row-level findings don't."

Click into the three that matter:
- **L-002 Rent changed (lower)**, Theo Alvarez 5A: $2,500 → $2,050, **−$450/mo**
- **L-001 Missing lease**, Dana Whitfield 4B: **$2,250/mo** of rent at risk
- **D-001 Deposit balance missing**, Mina Okafor 3A: **$1,800**, a liability kept separate from rent

Gates G10/G11 stay red until each is reviewed.

## 9 · Close (20 s)

Review the open critical, high and money findings in **Findings**, with reasons. Then go back to **06 Verify →
Issue release receipt**.
- The receipt carries the sha256 of the plan, dry run, package and verification run.
- *Stored receipt matches its hash* confirms it hasn't been altered.
- The Command Center now shows **Released** next to the continuity score.

**Optional for an engineering audience:**
- **Continuity Proof → Download PDF.** It has a new section with the three-way tie-out, gates and receipt hash.
- The MCP server (`./run.sh mcp`) exposes the same plan and dry run to an AI agent, with tenant names tokenized as
  `PERSON_…`. The agent can't approve anything.

---

**If something goes sideways:**
- **Drift banner:** the legacy exports changed. Click *Review & rebase*.
- **Dry run says the plan changed:** re-run it. Any mapping change invalidates the dry run by design.
- **Start over:** use the deal switcher → *Try sample acquisition*, or `POST /api/sample {"reset": true}`.
