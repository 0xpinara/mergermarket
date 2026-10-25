import os

os.environ.setdefault("MERGEPROOF_AI", "off")  # tests are deterministic and offline; AI tests opt in

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config, db, services  # noqa: E402
from app.ai import embeddings  # noqa: E402
from app.engine import metrics, runner  # noqa: E402


@pytest.fixture(autouse=True)
def _ai_off(monkeypatch):
    monkeypatch.setenv("MERGEPROOF_AI", "off")
    embeddings.reset_for_tests()
    yield
    embeddings.reset_for_tests()


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    db.init()
    return tmp_path / "test.db"


@pytest.fixture()
def client(tmpdb):
    from app.main import app
    with TestClient(app) as c:
        yield c


WS = {"X-Workspace": "ws_test_000001"}


@pytest.fixture()
def sample_run(tmpdb):
    """Sample deal with one completed audit. Returns (deal_id, findings, summary)."""
    with db.connect() as conn:
        did = services.load_sample(conn, "ws_test_000001")
        deal = dict(conn.execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone())
        runner.run_audit(conn, deal)
    with db.connect() as conn:
        fs = metrics.load_findings(conn, did)
        summary = metrics.summarize(conn, deal, None)
    return did, fs, summary


def by_rule(findings, rule_id):
    return [f for f in findings if f["rule_id"] == rule_id]


def by_subject(findings, needle):
    return [f for f in findings if needle in f["subject"]]
