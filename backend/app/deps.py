"""Request dependencies shared by every router."""
from __future__ import annotations

import re

from fastapi import Header, HTTPException, Query

_WS_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def workspace(x_workspace: str | None = Header(default=None), ws: str | None = Query(default=None)) -> str:
    """Per-browser workspace scoping. This is isolation for a demo, not authentication or tenant authorization."""
    w = x_workspace or ws
    if not w or not _WS_RE.match(w):
        raise HTTPException(400, "Missing or invalid workspace id.")
    return w


def get_deal(conn, deal_id: str, ws: str) -> dict:
    row = conn.execute("SELECT * FROM deals WHERE id=? AND workspace=?", (deal_id, ws)).fetchone()
    if row is None:
        raise HTTPException(404, "Audit workspace not found.")
    return dict(row)
