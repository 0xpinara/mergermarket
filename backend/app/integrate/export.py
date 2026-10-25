"""Offline export of the signed-off import package. MergeProof writes nothing to any vendor system: this zip is the
hand-off, and loading it is a person's job. The package is rebuilt deterministically from the plan and must
reproduce the signed-off dry run byte for byte, or the export is refused."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile

from .. import config
from . import dryrun, transforms
from . import plan as planmod

FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")
TEXT_TYPES = {"text", "name", "unit", "email"}
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def blueprint(p: dict, signed: dict | None) -> dict:
    """The mapping plan as data — no tenant rows. Safe to share with an implementation team."""
    return {
        "blueprint": "MergeProof integration plan", "status": "signed" if signed else "draft",
        "template": {k: p["template"][k] for k in ("id", "name", "version")},
        "plan_hash": p["plan_hash"], "plan_version": p["version"], "source_contract": p["contract_fp"],
        "tables": [{"table": t["name"], "import_file": t["file"], "legacy_file": t["source_file"]}
                   for t in p["tables"]],
        "columns": [{"target": f"{i['table']}.{i['column']}", "type": i["type"], "required": i["required"],
                     "financial": i["financial"], "sources": i["sources"], "transform": i["transform"],
                     "status": i["status"], "decided_by": (i.get("decision") or {}).get("by"),
                     "note": (i.get("decision") or {}).get("note")} for i in p["items"]],
        "signoff": {"reviewer": signed["reviewer"], "at": signed["created_at"]} if signed else None,
        "transform_allowlist": sorted(transforms.OPS),
    }


def _guarded_csv(table: dict, col_types: dict) -> tuple[bytes, int]:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(table["columns"])
    guarded = 0
    for r in table["rows"]:
        out = []
        for c in table["columns"]:
            v = r[c]
            if v and col_types.get(c) in TEXT_TYPES and v.startswith(FORMULA_LEAD):
                v = "'" + v
                guarded += 1
            out.append(v)
        w.writerow(out)
    return buf.getvalue().encode("utf-8"), guarded


def _files(conn, p: dict, so: dict | None) -> tuple[list[tuple[str, bytes]], dict, dryrun.Sim]:
    """Every file the manifest covers: import CSVs, crosswalks, lineage and the plan itself."""
    sim = dryrun.execute(conn, p)
    files, guards = [], {}
    for tb in p["template"]["tables"]:
        t = sim.tables[tb["name"]]
        types = {c["name"]: c["type"] for c in tb["columns"]}
        data, g = _guarded_csv(t, types)
        guards[tb["file"]] = g
        files.append((f"import/{tb['file']}", data))
        cw = io.StringIO()
        w = csv.writer(cw, lineterminator="\n")
        w.writerow(["legacy_key", "new_key", "legacy_file", "legacy_row"])
        for lin, row in zip(t["lineage"], t["rows"]):
            w.writerow([lin["legacy_key"] or "", row[tb["key"]], lin["file"], lin["row"]])
        files.append((f"crosswalk/{tb['name']}_crosswalk.csv", cw.getvalue().encode("utf-8")))
    lb = io.StringIO()
    w = csv.writer(lb, lineterminator="\n")
    w.writerow(["table", "new_key", "column", "legacy_file", "legacy_row", "legacy_columns", "transform"])
    for tb in p["template"]["tables"]:
        t = sim.tables[tb["name"]]
        for lin, row in zip(t["lineage"], t["rows"]):
            for col, l in lin["columns"].items():
                w.writerow([tb["name"], row[tb["key"]], col, lin["file"], lin["row"], "+".join(l["sources"]), l["op"]])
    files.append(("lineage.csv", lb.getvalue().encode("utf-8")))
    files.append(("plan.json", json.dumps(blueprint(p, so), indent=1, ensure_ascii=False).encode("utf-8")))
    return files, guards, sim


def manifest(conn, deal: dict, p: dict, dr: dict, so: dict | None, files=None, guards=None) -> dict:
    if files is None:
        files, guards, _ = _files(conn, p, so)
    return {
        "package": "MergeProof import package", "status": dryrun.BANNER,
        "synthetic": bool(deal["is_sample"]), "deal_id": deal["id"], "engine_version": config.ENGINE_VERSION,
        "template": {k: p["template"][k] for k in ("id", "name", "version")},
        "plan_hash": p["plan_hash"], "plan_version": p["version"], "source_contract": p["contract_fp"],
        "dry_run_id": dr["id"], "simulated_output_sha256": dr["output_hash"],
        "signoff": {"reviewer": so["reviewer"], "at": so["created_at"], "kind": "single reviewer"} if so else None,
        "files": [{"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for path, data in files],
        "formula_guarded_cells": guards,
        "instructions": ("Load the files in import/ in this order: " +
                         ", ".join(t["file"] for t in p["template"]["tables"]) +
                         ". Then export the target system's data and run MergeProof's post-import verification."),
    }


README = """MergeProof import package
=========================

{banner}

These files were generated from the signed-off integration plan {plan} (version {version}) and reproduce dry run
{dry} exactly. Nothing has been imported. MergeProof never writes to a destination system.

  import/      one CSV per target table, in load order: {order}
  crosswalk/   legacy key -> new key for every row (keep these: they make the load reversible and auditable)
  lineage.csv  for every output cell: legacy file, row, column(s) and the transform applied
  plan.json    the mapping plan itself (no tenant data)
  manifest.json  hashes of every file; formula-guarded cell counts

Free-text cells that a spreadsheet would execute as a formula (= + - @) are prefixed with an apostrophe.
Typed columns (keys, money, dates, codes) are validated instead and never altered.

After loading: export the destination's data and run post-import verification in MergeProof. Only that step can
catch defects introduced by the load itself.

{synthetic}"""


def build_zip(conn, deal: dict) -> tuple[bytes, str]:
    from . import readiness
    r = readiness.evaluate(conn, deal)
    if not r["export_ready"]:
        failing = [f"{x['id']} {x['label']}" for x in r["gates"] if x["phase"] != "verify" and x["status"] != "pass"]
        raise planmod.PlanError("Export unlocks after gates G1–G8 pass: " + "; ".join(failing))
    p = planmod.load(conn, deal["id"])
    dr = dryrun.latest(conn, deal["id"])
    so = readiness.latest_signoff(conn, deal["id"])
    files, guards, sim = _files(conn, p, so)
    if dryrun.output_hash(sim) != dr["output_hash"]:
        raise planmod.PlanError("The rebuilt files don't match the signed-off dry run. Run the dry run again.")
    man = manifest(conn, deal, p, dr, so, files, guards)
    files.append(("manifest.json", json.dumps(man, indent=1, ensure_ascii=False).encode("utf-8")))
    files.append(("README.txt", README.format(
        banner=dryrun.BANNER, plan=p["plan_hash"][:12], version=p["version"], dry=dr["id"],
        order=", ".join(t["file"] for t in p["template"]["tables"]),
        synthetic="All data in this package is synthetic demonstration data." if deal["is_sample"] else "").encode()))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in sorted(files):
            info = zipfile.ZipInfo(f"mergeproof_import/{path}", date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    data = buf.getvalue()
    planmod._event(conn, p, so["reviewer"], "exported", note=f"Import package exported (sha256 "
                                                             f"{hashlib.sha256(data).hexdigest()[:16]}…).")
    return data, hashlib.sha256(data).hexdigest()
