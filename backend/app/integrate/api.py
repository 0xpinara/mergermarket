"""HTTP routes for Plan → Dry run → Verify. Every route is scoped to the caller's workspace."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import db
from ..deps import get_deal, workspace
from . import dryrun, export, mapping, readiness
from . import plan as planmod

router = APIRouter(prefix="/api/deals/{deal_id}/integrate", tags=["integrate"])


class ReviewerIn(BaseModel):
    reviewer: str = Field(min_length=1, max_length=60)


class PlanIn(ReviewerIn):
    template_id: str = Field(min_length=1, max_length=80)
    replace: bool = False


class ItemIn(ReviewerIn):
    action: Literal["approve", "reject", "edit", "ignore", "reopen"]
    note: str | None = Field(default=None, max_length=1000)
    sources: list[str] | None = Field(default=None, max_length=4)
    transform: dict | None = None


class SignIn(ReviewerIn):
    acknowledge: bool = False


class RevokeIn(ReviewerIn):
    note: str | None = Field(default=None, max_length=1000)


def _who(body: ReviewerIn) -> str:
    r = body.reviewer.strip()
    if not r:
        raise HTTPException(422, "Add a reviewer name.")
    return r


def _status(conn, deal: dict) -> dict:
    from ..main import deal_status
    return deal_status(conn, deal)


def _plan(conn, deal_id: str) -> dict:
    p = planmod.load(conn, deal_id)
    if p is None:
        raise HTTPException(404, "No integration plan yet — choose a target template to start one.")
    return p


def _err(exc: planmod.PlanError):
    raise HTTPException(exc.status, str(exc))


def plan_view(conn, p: dict) -> dict:
    warnings = planmod.dependency_warnings(conn, p)
    items = []
    for i in p["items"]:
        items.append({**i, "live_warnings": warnings.get(i["id"], []), "bulk_eligible": mapping.bulk_eligible(i),
                      "needs_note": mapping.needs_note(i)})
    return {"id": p["id"], "template": {k: p["template"][k] for k in ("id", "name", "version", "notes", "synthetic",
                                                                     "source")},
            "template_tables": p["template"]["tables"], "version": p["version"], "plan_hash": p["plan_hash"],
            "contract_fp": p["contract_fp"], "tables": p["tables"], "items": items, "counts": planmod.counts(p),
            "drift": planmod.drift(conn, p), "created_at": p["created_at"], "updated_at": p["updated_at"]}


@router.get("")
def overview(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        deal = get_deal(conn, deal_id, ws)
        p = planmod.load(conn, deal_id)
        st = _status(conn, deal)
        dr = dryrun.latest(conn, deal_id)
        out = {"has_plan": p is not None, "templates": planmod.available_templates(conn, deal_id),
               "has_legacy": conn.execute("SELECT COUNT(*) FROM snapshots WHERE deal_id=? AND side='before'",
                                          (deal_id,)).fetchone()[0] > 0,
               "readiness": readiness.evaluate(conn, deal, st), "audit_state": st["state"], "banner": dryrun.BANNER}
        if p:
            out["plan"] = {"version": p["version"], "plan_hash": p["plan_hash"], "counts": planmod.counts(p),
                           "template": {k: p["template"][k] for k in ("id", "name", "version")}}
        if dr:
            out["dry_run"] = {k: dr[k] for k in ("id", "created_at", "totals", "predicted_kpis", "plan_hash",
                                                 "plan_version")}
        rc = readiness.latest_receipt(conn, deal_id)
        out["receipt"] = {"id": rc["id"], "sha256": rc["sha256"], "issued_at": rc["receipt"]["issued_at"]} if rc else None
        return out


@router.get("/templates")
def templates(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        get_deal(conn, deal_id, ws)
        return planmod.available_templates(conn, deal_id)


@router.get("/profile")
def profile(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        get_deal(conn, deal_id, ws)
        try:
            src = planmod.sources_for(conn, deal_id)
        except planmod.PlanError as exc:
            _err(exc)
        return src.profile


@router.post("/plan", status_code=201)
def create_plan(deal_id: str, body: PlanIn, ws: str = Depends(workspace)):
    who = _who(body)
    with db.connect() as conn:
        deal = get_deal(conn, deal_id, ws)
        if planmod.load(conn, deal_id) and not body.replace:
            raise HTTPException(409, "This deal already has a plan. Pass replace=true to rebuild its suggestions.")
        try:
            p = planmod.create(conn, deal, body.template_id, who)
        except planmod.PlanError as exc:
            _err(exc)
        except ValueError as exc:  # template validation
            raise HTTPException(422, str(exc)) from exc
        return plan_view(conn, p)


@router.get("/plan")
def get_plan(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        get_deal(conn, deal_id, ws)
        return plan_view(conn, _plan(conn, deal_id))


@router.post("/plan/items/{item_id}")
def act(deal_id: str, item_id: str, body: ItemIn, ws: str = Depends(workspace)):
    who = _who(body)
    note = (body.note or "").strip() or None
    with db.connect() as conn:
        get_deal(conn, deal_id, ws)
        p = _plan(conn, deal_id)
        try:
            planmod.act(conn, p, item_id, body.action, who, note, body.sources, body.transform)
        except planmod.PlanError as exc:
            _err(exc)
        return plan_view(conn, p)


@router.post("/plan/bulk-approve")
def bulk(deal_id: str, body: ReviewerIn, ws: str = Depends(workspace)):
    who = _who(body)
    with db.connect() as conn:
        get_deal(conn, deal_id, ws)
        p = _plan(conn, deal_id)
        done = planmod.bulk_approve(conn, p, who)
        return {"approved": done, "plan": plan_view(conn, p)}


@router.post("/plan/rebase")
def rebase(deal_id: str, body: ReviewerIn, ws: str = Depends(workspace)):
    who = _who(body)
    with db.connect() as conn:
        get_deal(conn, deal_id, ws)
        p = _plan(conn, deal_id)
        try:
            planmod.rebase(conn, p, who)
        except planmod.PlanError as exc:
            _err(exc)
        return plan_view(conn, p)


@router.get("/plan/items/{item_id}/evidence")
def evidence(deal_id: str, item_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        get_deal(conn, deal_id, ws)
        p = _plan(conn, deal_id)
        item = next((i for i in p["items"] if i["id"] == item_id), None)
        if item is None:
            raise HTTPException(404, "No such column in this plan.")
        b = next(t for t in p["tables"] if t["name"] == item["table"])
        try:
            src = planmod.sources_for(conn, deal_id)
        except planmod.PlanError as exc:
            _err(exc)
        cols = []
        if b["snapshot_id"] and b["snapshot_id"] in src.files:
            names = {s for c in item["candidates"] for s in c["sources"]} | set(item["sources"]) | \
                    {s for e in item["excluded"] for s in e["sources"]}
            cols = [c for c in src.files[b["snapshot_id"]]["columns"] if c["column"] in names]
        tb = next(t for t in p["template"]["tables"] if t["name"] == item["table"])
        col = next(c for c in tb["columns"] if c["name"] == item["column"])
        history = [e for e in planmod.events(conn, deal_id, 500) if e["item_id"] == item_id]
        return {"item": item, "target": col, "source_file": b["source_file"], "profile": cols,
                "live_warnings": planmod.dependency_warnings(conn, p).get(item_id, []), "history": history,
                "needs_note": mapping.needs_note(item)}


@router.get("/events")
def events(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        get_deal(conn, deal_id, ws)
        return planmod.events(conn, deal_id)


@router.post("/dry-run")
def run_dry(deal_id: str, body: ReviewerIn, ws: str = Depends(workspace)):
    who = _who(body)
    with db.connect() as conn:
        deal = get_deal(conn, deal_id, ws)
        p = _plan(conn, deal_id)
        if planmod.drift(conn, p)["changed"]:
            raise HTTPException(409, "The legacy exports changed since this plan was built. Rebase the plan first.")
        try:
            return dryrun.run(conn, deal, p, who)
        except planmod.PlanError as exc:
            _err(exc)


@router.get("/dry-run")
def get_dry(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        get_deal(conn, deal_id, ws)
        dr = dryrun.latest(conn, deal_id)
        if dr is None:
            raise HTTPException(404, "No dry run yet.")
        p = planmod.load(conn, deal_id)
        dr["current"] = bool(p and dr["plan_hash"] == p["plan_hash"])
        return dr


@router.get("/dry-run/preview")
def preview(deal_id: str, table: str = Query(..., max_length=60), offset: int = Query(0, ge=0),
            limit: int = Query(50, ge=1, le=200), rejected: bool = False, ws: str = Depends(workspace)):
    with db.connect() as conn:
        get_deal(conn, deal_id, ws)
        p = _plan(conn, deal_id)
        try:
            return dryrun.preview(conn, p, table, offset, limit, rejected)
        except planmod.PlanError as exc:
            _err(exc)


@router.post("/signoff")
def signoff(deal_id: str, body: SignIn, ws: str = Depends(workspace)):
    who = _who(body)
    with db.connect() as conn:
        deal = get_deal(conn, deal_id, ws)
        try:
            readiness.sign(conn, deal, who, body.acknowledge, _status(conn, deal))
        except planmod.PlanError as exc:
            _err(exc)
        return readiness.evaluate(conn, deal, _status(conn, deal))


@router.post("/signoff/revoke")
def revoke(deal_id: str, body: RevokeIn, ws: str = Depends(workspace)):
    who = _who(body)
    with db.connect() as conn:
        deal = get_deal(conn, deal_id, ws)
        try:
            readiness.revoke(conn, deal, who, (body.note or "").strip() or None)
        except planmod.PlanError as exc:
            _err(exc)
        return readiness.evaluate(conn, deal, _status(conn, deal))


@router.get("/readiness")
def get_readiness(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        deal = get_deal(conn, deal_id, ws)
        return readiness.evaluate(conn, deal, _status(conn, deal))


@router.get("/export.zip")
def export_zip(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        deal = get_deal(conn, deal_id, ws)
        try:
            data, digest = export.build_zip(conn, deal)
        except planmod.PlanError as exc:
            _err(exc)
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="mergeproof_import_package.zip"', "X-Package-SHA256": digest})


@router.get("/blueprint.json")
def blueprint(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        deal = get_deal(conn, deal_id, ws)
        p = _plan(conn, deal_id)
        r = readiness.evaluate(conn, deal, _status(conn, deal))
        so = readiness.latest_signoff(conn, deal_id) if r["export_ready"] else None
        return Response(db.dumps(export.blueprint(p, so)), media_type="application/json", headers={
            "Content-Disposition": 'attachment; filename="mergeproof_integration_plan.json"'})


@router.get("/verify")
def verify(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        deal = get_deal(conn, deal_id, ws)
        return {"comparison": readiness.compare(conn, deal),
                "readiness": readiness.evaluate(conn, deal, _status(conn, deal))}


@router.post("/receipt", status_code=201)
def receipt(deal_id: str, body: ReviewerIn, ws: str = Depends(workspace)):
    who = _who(body)
    with db.connect() as conn:
        deal = get_deal(conn, deal_id, ws)
        try:
            return readiness.build_receipt(conn, deal, who, _status(conn, deal))
        except planmod.PlanError as exc:
            _err(exc)


@router.get("/receipt")
def get_receipt(deal_id: str, ws: str = Depends(workspace)):
    with db.connect() as conn:
        get_deal(conn, deal_id, ws)
        rc = readiness.latest_receipt(conn, deal_id)
        if rc is None:
            raise HTTPException(404, "No release receipt yet.")
        return rc
