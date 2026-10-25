"""MergeProof API + static frontend."""
from __future__ import annotations

import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import config, db, ingest, reports, services
from .deps import get_deal, workspace
from .ai import embeddings
from .engine import metrics, runner
from .integrate import api as integrate_api
from .schema import ENTITIES, schema_payload

log = logging.getLogger("mergeproof")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    embeddings.warm_in_background()
    yield


app = FastAPI(title="MergeProof", version="1.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json",
              lifespan=lifespan)


@app.middleware("http")
async def _headers(request: Request, call_next):
    try:
        response = await call_next(request)
    except Exception as exc:  # log the type and route only — never row contents
        log.error("unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path)
        response = JSONResponse({"detail": "Something went wrong on our side. Please retry."}, status_code=500)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    if request.url.path.startswith("/api"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


_deal = get_deal


def _snapshot(conn, sid: str, ws: str) -> tuple[dict, dict]:
    row = conn.execute("SELECT s.* FROM snapshots s JOIN deals d ON d.id=s.deal_id WHERE s.id=? AND d.workspace=?",
                       (sid, ws)).fetchone()
    if row is None:
        raise HTTPException(404, "Snapshot not found.")
    snap = dict(row)
    return snap, _deal(conn, snap["deal_id"], ws)


def _finding(conn, fid: str, ws: str) -> tuple[dict, dict]:
    row = conn.execute("SELECT f.* FROM findings f JOIN deals d ON d.id=f.deal_id WHERE f.id=? AND d.workspace=?",
                       (fid, ws)).fetchone()
    if row is None:
        raise HTTPException(404, "Finding not found.")
    f = dict(row)
    f["payload"] = json.loads(f.pop("payload_json"))
    return f, _deal(conn, f["deal_id"], ws)


# --------------------------------------------------------------------------- models

def _clean(v: str | None, n: int) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v[:n] or None


def _iso(v: str | None) -> str | None:
    if not v:
        return None
    try:
        return date.fromisoformat(v.strip()).isoformat()
    except ValueError as exc:
        raise ValueError("Use a YYYY-MM-DD date.") from exc


def _url(v: str | None) -> str | None:
    v = (v or "").strip()
    if not v:
        return None
    if not re.match(r"^https?://[^\s<>\"']+$", v, re.I) or len(v) > 500:
        raise ValueError("Enter a full http(s):// link.")
    return v


class DealIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    industry: str | None = Field(default=None, max_length=120)
    acquirer: str | None = Field(default=None, max_length=120)
    target_business: str | None = Field(default=None, max_length=120)
    deal_date: str | None = None
    old_system: str | None = Field(default=None, max_length=120)
    new_system: str | None = Field(default=None, max_length=120)
    source_notes: str | None = Field(default=None, max_length=2000)
    source_ref: str | None = Field(default=None, max_length=500)
    materiality_rent_cents: int | None = Field(default=None, ge=0, le=10**11)
    materiality_deposit_cents: int | None = Field(default=None, ge=0, le=10**11)

    @field_validator("deal_date")
    @classmethod
    def _date(cls, v):
        return _iso(v)


class DealPatch(DealIn):
    title: str | None = Field(default=None, min_length=1, max_length=160)


class MappingIn(BaseModel):
    entity: str
    mapping: dict[str, str | None]

    @field_validator("entity")
    @classmethod
    def _e(cls, v):
        if v not in ENTITIES:
            raise ValueError("Unknown record type.")
        return v


class ReviewIn(BaseModel):
    action: Literal["investigate", "confirm_match", "expected_exception", "resolve", "reopen", "comment"]
    reviewer: str = Field(min_length=1, max_length=60)
    note: str | None = Field(default=None, max_length=2000)
    candidate: str | None = Field(default=None, max_length=200)


PILOT = ("researching", "contacted", "interviewing", "pilot_proposed", "pilot_active", "not_a_fit")


class IntelIn(BaseModel):
    company_name: str = Field(min_length=1, max_length=160)
    sector: str | None = Field(default=None, max_length=120)
    location: str | None = Field(default=None, max_length=120)
    event_date: str | None = None
    website: str | None = None
    source_link: str | None = None
    notes: str | None = Field(default=None, max_length=4000)
    contact_role: str | None = Field(default=None, max_length=120)
    pilot_status: Literal[PILOT] = "researching"  # type: ignore[valid-type]
    linked_deal_id: str | None = Field(default=None, max_length=40)

    @field_validator("event_date")
    @classmethod
    def _date(cls, v):
        return _iso(v)

    @field_validator("website", "source_link")
    @classmethod
    def _link(cls, v):
        return _url(v)


class SampleIn(BaseModel):
    reset: bool = False


# --------------------------------------------------------------------------- views

def deal_status(conn, deal: dict) -> dict:
    snaps = [dict(r) for r in conn.execute("SELECT * FROM snapshots WHERE deal_id=?", (deal["id"],))]
    problems, skipped = runner.check_ready(snaps) if snaps else (["Add exports to begin."], [])
    run = conn.execute("SELECT id, fingerprint, started_at FROM audit_runs WHERE deal_id=? AND status='completed' "
                       "ORDER BY started_at DESC LIMIT 1", (deal["id"],)).fetchone()
    fp = runner.fingerprint(conn, deal, snaps) if snaps else None
    counts = conn.execute("SELECT SUM(status IN ('open','investigating') OR needs_rereview=1), "
                          "SUM(status IN ('expected','resolved') AND needs_rereview=0) "
                          "FROM findings WHERE deal_id=? AND active=1", (deal["id"],)).fetchone()
    open_n, closed_n = counts[0] or 0, counts[1] or 0
    if not snaps:
        state = "draft"
    elif problems:
        state = "needs_input"
    elif run is None:
        state = "ready"
    elif run["fingerprint"] != fp:
        state = "stale"
    elif open_n == 0:
        state = "complete"
    elif closed_n:
        state = "in_review"
    else:
        state = "audited"
    return {"state": state, "problems": problems, "skipped": skipped, "last_run_at": run["started_at"] if run else None,
            "open_findings": open_n, "closed_findings": closed_n, "fingerprint": fp}


def deal_view(conn, deal: dict, with_snapshots: bool = True) -> dict:
    out = {k: deal[k] for k in ("id", "title", "industry", "acquirer", "target_business", "deal_date", "old_system",
                                "new_system", "source_notes", "source_ref", "created_at", "updated_at",
                                "materiality_rent_cents", "materiality_deposit_cents")}
    out["is_sample"] = bool(deal["is_sample"])
    out["status"] = deal_status(conn, deal)
    if with_snapshots:
        snaps = [dict(r) for r in conn.execute("SELECT * FROM snapshots WHERE deal_id=? ORDER BY side, entity, "
                                               "file_name", (deal["id"],))]
        out["snapshots"] = [services.snapshot_view(conn, s) for s in snaps]
    return out


# --------------------------------------------------------------------------- routes: meta

@app.get("/api/health")
def health():
    return {"ok": True, "engine": config.ENGINE_VERSION}


@app.get("/api/ai")
def ai_card():
    """What the local AI does in MergeProof, its current status, and the latest evaluation results."""
    results = None
    path = config.BACKEND_DIR / "eval" / "results.json"
    if path.is_file():
        try:
            results = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            results = None
    return {
        "status": embeddings.status(),
        "does": ["Suggests consolidated work orders that may be a legacy request with its description rewritten, "
                 "only when the rules find no candidate, filtered to the same property and a 30-day window.",
                 "Suggests a column for fields the alias rules couldn't map."],
        "never": ["Pairs records, maps columns or changes any amount on its own.",
                  "Contributes to rent at risk, deposit totals or the continuity score.",
                  "Sends data anywhere: the model runs on this server."],
        "off_switch": "MERGEPROOF_AI=off",
        "evaluation": results,
    }


@app.get("/api/schema")
def get_schema():
    from .engine.rules import RULES
    return {"entities": schema_payload(), "rules": RULES,
            "limits": {"max_upload_mb": config.MAX_UPLOAD_BYTES // (1024 * 1024),
                       "max_rows_per_file": config.MAX_ROWS_PER_FILE,
                       "max_files_per_request": config.MAX_FILES_PER_REQUEST,
                       "file_types": sorted(ingest.ALLOWED_EXT)}}


# --------------------------------------------------------------------------- routes: deals

@app.get("/api/deals")
def list_deals(ws: str = Depends(workspace)):
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM deals WHERE workspace=? ORDER BY updated_at DESC", (ws,))]
        return [deal_view(conn, d, with_snapshots=False) for d in rows]


@app.post("/api/deals", status_code=201)
def create_deal(body: DealIn, ws: str = Depends(workspace)):
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM deals WHERE workspace=?", (ws,)).fetchone()[0]
        if n >= 50:
            raise HTTPException(400, "This demo workspace already has 50 audits. Delete one to add more.")
        did = services.create_deal(conn, ws, body.model_dump())
        return deal_view(conn, _deal(conn, did, ws))


@app.get("/api/deals/{deal_id}")
def get_deal(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        return deal_view(conn, _deal(conn, deal_id, ws))


@app.patch("/api/deals/{deal_id}")
def patch_deal(deal_id: str, body: DealPatch, ws: str = Depends(workspace)):
    with db.connect() as conn:
        _deal(conn, deal_id, ws)
        raw = body.model_dump(exclude_unset=True)
        data = {k: (v.strip() if isinstance(v, str) else v) or None for k, v in raw.items()
                if k in services.DEAL_FIELDS}
        data.update({k: raw[k] for k in services.MATERIALITY_FIELDS if k in raw})  # 0 is a valid threshold
        if "title" in data and not data["title"]:
            raise HTTPException(422, "Title can't be empty.")
        if data:
            sets = ", ".join(f"{k}=?" for k in data)
            conn.execute(f"UPDATE deals SET {sets}, updated_at=? WHERE id=?", (*data.values(), db.now(), deal_id))
        return deal_view(conn, _deal(conn, deal_id, ws))


@app.delete("/api/deals/{deal_id}", status_code=204)
def delete_deal(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        _deal(conn, deal_id, ws)
        conn.execute("UPDATE intel_notes SET linked_deal_id=NULL WHERE linked_deal_id=?", (deal_id,))
        conn.execute("DELETE FROM deals WHERE id=?", (deal_id,))
    return Response(status_code=204)


@app.post("/api/sample")
def sample(body: SampleIn | None = None, ws: str = Depends(workspace)):
    body = body or SampleIn()
    with db.connect() as conn:
        existing = conn.execute("SELECT id FROM deals WHERE workspace=? AND is_sample=1 ORDER BY created_at",
                                (ws,)).fetchall()
        if existing and not body.reset:
            return {"deal": deal_view(conn, _deal(conn, existing[0]["id"], ws)), "created": False}
        for r in existing:
            conn.execute("DELETE FROM deals WHERE id=?", (r["id"],))
        did = services.load_sample(conn, ws)
        return {"deal": deal_view(conn, _deal(conn, did, ws)), "created": True}


@app.get("/api/sample/files")
def sample_files():
    return services.sample_files()


@app.get("/api/sample/files/{side}/{name}")
def sample_file(side: str, name: str):
    p = services.sample_path(side, name)
    if not p:
        raise HTTPException(404, "Sample file not found.")
    return FileResponse(p, media_type="text/csv", filename=p.name)


# --------------------------------------------------------------------------- routes: snapshots

@app.post("/api/deals/{deal_id}/snapshots")
async def upload(deal_id: str, side: str = Form(...), confirm_synthetic: str = Form("false"),
                 files: list[UploadFile] = File(...), ws: str = Depends(workspace)):
    if side not in ("before", "after"):
        raise HTTPException(422, "side must be 'before' or 'after'.")
    if confirm_synthetic.lower() not in ("true", "1", "yes", "on"):
        raise HTTPException(400, "Please confirm the files contain only synthetic or redacted data — this is a "
                                 "public demo environment.")
    if not files:
        raise HTTPException(422, "Choose at least one file.")
    if len(files) > config.MAX_FILES_PER_REQUEST:
        raise HTTPException(400, f"Upload at most {config.MAX_FILES_PER_REQUEST} files at a time.")
    payloads = []
    for f in files:
        data = await f.read(config.MAX_UPLOAD_BYTES + 1)
        payloads.append((ingest.sanitize_filename(f.filename), data))
    created, errors = [], []
    with db.connect() as conn:
        _deal(conn, deal_id, ws)
        existing = conn.execute("SELECT COUNT(*) FROM snapshots WHERE deal_id=?", (deal_id,)).fetchone()[0]
        for name, data in payloads:
            try:
                tables = ingest.parse_upload(name, data)
            except ingest.UploadError as exc:
                errors.append({"file": name, "message": str(exc)})
                continue
            except Exception as exc:
                log.warning("parse failure %s for upload", type(exc).__name__)
                errors.append({"file": name, "message": f"{name}: couldn't be read. Check it is a valid CSV/XLSX export."})
                continue
            for t in tables:
                if existing >= config.MAX_SNAPSHOTS_PER_DEAL:
                    errors.append({"file": t.label, "message": "Too many files in this audit for the demo limit."})
                    break
                sid = services.add_snapshot(conn, deal_id, side, t, origin="upload")
                existing += 1
                created.append(services.snapshot_view(conn, dict(conn.execute(
                    "SELECT * FROM snapshots WHERE id=?", (sid,)).fetchone())))
        conn.execute("UPDATE deals SET updated_at=? WHERE id=?", (db.now(), deal_id))
    if not created and errors:
        return JSONResponse({"created": [], "errors": errors, "detail": errors[0]["message"]}, status_code=400)
    return {"created": created, "errors": errors}


@app.patch("/api/snapshots/{sid}")
def patch_snapshot(sid: str, body: MappingIn, ws: str = Depends(workspace)):
    with db.connect() as conn:
        snap, deal = _snapshot(conn, sid, ws)
        headers = json.loads(snap["headers_json"])
        spec = ENTITIES[body.entity]
        mapping = {}
        for k, col in body.mapping.items():
            if col in (None, ""):
                continue
            if spec.field(k) is None:
                raise HTTPException(422, f"Unknown field '{k}' for {spec.label.lower()}.")
            if col not in headers:
                raise HTTPException(422, f"Column '{col}' isn't in {snap['file_name']}.")
            mapping[k] = col
        if len(set(mapping.values())) != len(mapping):
            raise HTTPException(422, "Each column can map to only one field.")
        meta = json.loads(snap["mapping_meta_json"])
        if body.entity != snap["entity"]:
            meta["fields"] = ingest.suggest_mapping(body.entity, headers)
        conn.execute("UPDATE snapshots SET entity=?, mapping_json=?, mapping_meta_json=?, user_mapped=1, updated_at=? "
                     "WHERE id=?", (body.entity, db.dumps(mapping), db.dumps(meta), db.now(), sid))
        conn.execute("UPDATE deals SET updated_at=? WHERE id=?", (db.now(), deal["id"]))
        return services.snapshot_view(conn, dict(conn.execute("SELECT * FROM snapshots WHERE id=?", (sid,)).fetchone()))


@app.post("/api/snapshots/{sid}/suggest")
def suggest(sid: str, entity: str = Query(...), ws: str = Depends(workspace)):
    if entity not in ENTITIES:
        raise HTTPException(422, "Unknown record type.")
    with db.connect() as conn:
        snap, _ = _snapshot(conn, sid, ws)
        meta = ingest.suggest_mapping(entity, json.loads(snap["headers_json"]))
        return {"entity": entity, "mapping": {k: v["column"] for k, v in meta.items()}, "meta": meta}


@app.delete("/api/snapshots/{sid}", status_code=204)
def delete_snapshot(sid: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        snap, deal = _snapshot(conn, sid, ws)
        conn.execute("DELETE FROM snapshots WHERE id=?", (sid,))
        conn.execute("UPDATE deals SET updated_at=? WHERE id=?", (db.now(), deal["id"]))
    return Response(status_code=204)


@app.get("/api/snapshots/{sid}/rows")
def snapshot_rows(sid: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500),
                  ws: str = Depends(workspace)):
    with db.connect() as conn:
        snap, _ = _snapshot(conn, sid, ws)
        rows = [{"row": r["row_number"], "values": json.loads(r["raw_json"])} for r in conn.execute(
            "SELECT row_number, raw_json FROM source_rows WHERE snapshot_id=? ORDER BY row_number LIMIT ? OFFSET ?",
            (sid, limit, offset))]
        return {"headers": json.loads(snap["headers_json"]), "rows": rows, "total": snap["row_count"]}


# --------------------------------------------------------------------------- routes: audit

@app.post("/api/deals/{deal_id}/audit")
def run(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        deal = _deal(conn, deal_id, ws)
        try:
            result = runner.run_audit(conn, deal)
        except runner.AuditBlocked as exc:
            raise HTTPException(409, {"message": "The audit can't run yet.", "problems": exc.problems})
    with db.connect() as conn:
        deal = _deal(conn, deal_id, ws)
        return {"run": result, "summary": metrics.summarize(conn, deal, result["fingerprint"])}


@app.get("/api/deals/{deal_id}/summary")
def summary(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        deal = _deal(conn, deal_id, ws)
        st = deal_status(conn, deal)
        return {"status": st, **metrics.summarize(conn, deal, st["fingerprint"])}


@app.get("/api/deals/{deal_id}/findings")
def findings(deal_id: str, include_inactive: bool = False, ws: str = Depends(workspace)):
    with db.connect() as conn:
        _deal(conn, deal_id, ws)
        fs = metrics.load_findings(conn, deal_id, include_inactive=True)
        _, _, counted = metrics.attribute_rent([f for f in fs if f["active"]])
        deal = _deal(conn, deal_id, ws)
        rows = [metrics.finding_row(f, counted, deal) for f in fs if include_inactive or f["active"]]
        rows.sort(key=lambda r: (not r["active"], metrics.SEVERITY_ORDER.get(r["severity"], 9),
                                 -(r["rent_risk_cents"] + r["deposit_cents"] + r["pending_rent_cents"]),
                                 r["rule_id"], r["subject"]))
        return rows


@app.get("/api/findings/{fid}")
def finding(fid: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        f, deal = _finding(conn, fid, ws)
        fs = metrics.load_findings(conn, deal["id"])
        _, _, counted = metrics.attribute_rent(fs)
        events = [dict(r) for r in conn.execute(
            "SELECT id, reviewer, action, note, from_status, to_status, payload_json, created_at FROM review_events "
            "WHERE finding_id=? ORDER BY created_at, id", (fid,))]
        for e in events:
            e["payload"] = json.loads(e.pop("payload_json") or "null")
        from .engine.rules import RULES
        p = f["payload"]
        related = []
        lk = (p.get("keys") or {}).get("lease")
        if lk:
            for o in fs:
                if o["id"] != fid and (o["payload"].get("keys") or {}).get("lease") == lk:
                    related.append({"id": o["id"], "rule_id": o["rule_id"], "title": o["title"],
                                    "severity": o["severity"], "status": o["status"],
                                    "rent_risk_cents": o["rent_risk_cents"], "deposit_cents": o["deposit_cents"]})
        override = conn.execute("SELECT target_key, reviewer, created_at FROM match_overrides WHERE finding_id=? "
                                "AND revoked_at IS NULL ORDER BY created_at DESC LIMIT 1", (fid,)).fetchone()
        return {
            **metrics.finding_row(f, counted, deal), "payload": p, "events": events, "related": related,
            "rule": RULES.get(f["rule_id"]), "labels": runner.labels_for(deal), "deal_id": deal["id"],
            "override": dict(override) if override else None, "first_run_id": f["first_run_id"],
            "last_run_id": f["last_run_id"], "created_at": f["created_at"],
        }


TRANSITIONS = {
    "investigate": ({"open"}, "investigating"),
    "confirm_match": ({"open", "investigating"}, "resolved"),
    "expected_exception": ({"open", "investigating", "resolved"}, "expected"),
    "resolve": ({"open", "investigating", "expected"}, "resolved"),
    "reopen": ({"investigating", "expected", "resolved"}, "open"),
    "comment": (None, None),
}


VERB = {"investigate": "start investigating", "confirm_match": "confirm a match for",
        "expected_exception": "mark as expected", "resolve": "resolve", "reopen": "reopen", "comment": "comment on"}
STATUS_WORD = {"open": "open", "investigating": "under investigation", "expected": "marked as an expected exception",
               "resolved": "resolved"}


@app.post("/api/findings/{fid}/review")
def review(fid: str, body: ReviewIn, ws: str = Depends(workspace)):
    note = (body.note or "").strip() or None
    reviewer = body.reviewer.strip()
    if not reviewer:
        raise HTTPException(422, "Add a reviewer name.")
    with db.connect() as conn:
        f, deal = _finding(conn, fid, ws)
        has_override = conn.execute("SELECT 1 FROM match_overrides WHERE finding_id=? AND revoked_at IS NULL",
                                    (fid,)).fetchone() is not None
        # A confirmed match makes its review finding disappear on the next run; reopening it is how the
        # reviewer undoes that decision, so it stays allowed while the override is live.
        if not f["active"] and body.action != "comment" and not (body.action == "reopen" and has_override):
            raise HTTPException(409, "This finding is no longer detected in the latest run. Rerun the audit first.")
        allowed, to_status = TRANSITIONS[body.action]
        if allowed is not None and f["status"] not in allowed:
            if f["status"] == to_status:
                raise HTTPException(409, f"This finding is already {STATUS_WORD[f['status']]}.")
            raise HTTPException(409, f"Can't {VERB[body.action]} a finding that is {STATUS_WORD[f['status']]}. "
                                     f"Reopen it first.")
        if body.action in ("expected_exception", "comment") and (not note or len(note) < 3):
            raise HTTPException(422, "A short reason is required." if body.action == "expected_exception"
                                else "Write a comment first.")
        ts = db.now()
        payload = None
        resolution = f["resolution"]
        if body.action == "confirm_match":
            cands = f["payload"].get("candidates") or []
            cand = next((c for c in cands if c["target_key"] == body.candidate), None)
            if cand is None:
                raise HTTPException(422, "Choose one of the listed candidates.")
            if cand.get("taken_by"):
                raise HTTPException(409, "That record is already matched to another legacy record.")
            src = f["payload"].get("source") or {}
            conn.execute("INSERT INTO match_overrides (id, deal_id, entity, source_key, target_key, finding_id, "
                         "reviewer, created_at) VALUES (?,?,?,?,?,?,?,?)",
                         (db.new_id("ovr"), deal["id"], src.get("entity"), src.get("key"), cand["target_key"], fid,
                          reviewer, ts))
            payload = {"candidate": cand["target_key"], "candidate_id": (cand.get("record") or {}).get("id"),
                       "score": cand["score"]}
            resolution = "match_confirmed"
        elif body.action == "reopen":
            n = conn.execute("UPDATE match_overrides SET revoked_at=? WHERE finding_id=? AND revoked_at IS NULL",
                             (ts, fid)).rowcount
            if n:
                payload = {"revoked_match": True}
            resolution = None
        elif body.action == "expected_exception":
            resolution = "expected_exception"
        elif body.action == "resolve":
            resolution = "resolved"
        elif body.action == "investigate":
            resolution = None
        new_status = to_status or f["status"]
        conn.execute("INSERT INTO review_events (id, finding_id, deal_id, reviewer, action, note, from_status, "
                     "to_status, payload_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (db.new_id("rev"), fid, deal["id"], reviewer, body.action, note, f["status"], new_status,
                      db.dumps(payload) if payload else None, ts))
        if body.action != "comment":
            conn.execute("UPDATE findings SET status=?, resolution=?, reviewed_hash=evidence_hash, needs_rereview=0, "
                         "updated_at=? WHERE id=?", (new_status, resolution, ts, fid))
        conn.execute("UPDATE deals SET updated_at=? WHERE id=?", (ts, deal["id"]))
    return finding(fid, ws)


@app.get("/api/deals/{deal_id}/reviews")
def reviews(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        _deal(conn, deal_id, ws)
        return [dict(r) for r in conn.execute(
            "SELECT e.id, e.finding_id, e.reviewer, e.action, e.note, e.from_status, e.to_status, e.created_at, "
            "f.title, f.subject, f.rule_id, f.severity FROM review_events e JOIN findings f ON f.id=e.finding_id "
            "WHERE e.deal_id=? ORDER BY e.created_at DESC, e.id DESC LIMIT 500", (deal_id,))]


@app.get("/api/deals/{deal_id}/unitmap")
def unitmap(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        _deal(conn, deal_id, ws)
        return metrics.unit_map(conn, deal_id)


@app.get("/api/deals/{deal_id}/report.pdf")
def report_pdf(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        deal = _deal(conn, deal_id, ws)
        data = reports.collect(conn, deal, deal_status(conn, deal))
        if not data["summary"].get("has_run"):
            raise HTTPException(409, "Run the audit before exporting a report.")
        pdf = reports.render_pdf(data)
    fname = f"MergeProof-Continuity-Report-{reports.slug(deal['title'])}.pdf"
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/api/deals/{deal_id}/findings.csv")
def findings_csv(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        deal = _deal(conn, deal_id, ws)
        data = reports.collect(conn, deal, deal_status(conn, deal))
        if not data["summary"].get("has_run"):
            raise HTTPException(409, "Run the audit before exporting findings.")
        body = reports.render_csv(data)
    fname = f"MergeProof-Findings-{reports.slug(deal['title'])}.csv"
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/api/deals/{deal_id}/report")
def report_json(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        deal = _deal(conn, deal_id, ws)
        return reports.collect(conn, deal, deal_status(conn, deal))


# --------------------------------------------------------------------------- routes: deal intelligence

def _intel_check_link(conn, ws: str, linked: str | None) -> None:
    if linked and conn.execute("SELECT 1 FROM deals WHERE id=? AND workspace=?", (linked, ws)).fetchone() is None:
        raise HTTPException(422, "Linked audit not found in this workspace.")


@app.get("/api/intel")
def intel_list(ws: str = Depends(workspace)):
    with db.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM intel_notes WHERE workspace=? ORDER BY updated_at DESC",
                                              (ws,))]


@app.post("/api/intel", status_code=201)
def intel_create(body: IntelIn, ws: str = Depends(workspace)):
    with db.connect() as conn:
        _intel_check_link(conn, ws, body.linked_deal_id)
        if conn.execute("SELECT COUNT(*) FROM intel_notes WHERE workspace=?", (ws,)).fetchone()[0] >= 200:
            raise HTTPException(400, "Demo limit of 200 notes reached.")
        iid, ts = db.new_id("intel"), db.now()
        d = body.model_dump()
        conn.execute("INSERT INTO intel_notes (id, workspace, company_name, sector, location, event_date, website, "
                     "source_link, notes, contact_role, pilot_status, linked_deal_id, created_at, updated_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (iid, ws, d["company_name"].strip(), _clean(d["sector"], 120), _clean(d["location"], 120),
                      d["event_date"], d["website"], d["source_link"], _clean(d["notes"], 4000),
                      _clean(d["contact_role"], 120), d["pilot_status"], d["linked_deal_id"] or None, ts, ts))
        return dict(conn.execute("SELECT * FROM intel_notes WHERE id=?", (iid,)).fetchone())


@app.put("/api/intel/{iid}")
def intel_update(iid: str, body: IntelIn, ws: str = Depends(workspace)):
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM intel_notes WHERE id=? AND workspace=?", (iid, ws)).fetchone() is None:
            raise HTTPException(404, "Note not found.")
        _intel_check_link(conn, ws, body.linked_deal_id)
        d = body.model_dump()
        conn.execute("UPDATE intel_notes SET company_name=?, sector=?, location=?, event_date=?, website=?, "
                     "source_link=?, notes=?, contact_role=?, pilot_status=?, linked_deal_id=?, updated_at=? "
                     "WHERE id=?",
                     (d["company_name"].strip(), _clean(d["sector"], 120), _clean(d["location"], 120), d["event_date"],
                      d["website"], d["source_link"], _clean(d["notes"], 4000), _clean(d["contact_role"], 120),
                      d["pilot_status"], d["linked_deal_id"] or None, db.now(), iid))
        return dict(conn.execute("SELECT * FROM intel_notes WHERE id=?", (iid,)).fetchone())


@app.delete("/api/intel/{iid}", status_code=204)
def intel_delete(iid: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        n = conn.execute("DELETE FROM intel_notes WHERE id=? AND workspace=?", (iid, ws)).rowcount
        if not n:
            raise HTTPException(404, "Note not found.")
    return Response(status_code=204)


# --------------------------------------------------------------------------- routes: integrate (plan → dry run → verify)

app.include_router(integrate_api.router)


# --------------------------------------------------------------------------- static frontend

@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def api_404(rest: str):
    raise HTTPException(404, "Not found.")


if (config.STATIC_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=config.STATIC_DIR / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    index = config.STATIC_DIR / "index.html"
    candidate = (config.STATIC_DIR / path).resolve()
    if path and candidate.is_file() and config.STATIC_DIR.resolve() in candidate.parents:
        return FileResponse(candidate)
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "Frontend not built. Run `npm run build` in frontend/."}, status_code=404)
