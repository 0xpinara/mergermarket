from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
REPO_DIR = BACKEND_DIR.parent

DATA_DIR = Path(os.environ.get("MERGEPROOF_DATA_DIR", BACKEND_DIR / "data"))
DB_PATH = Path(os.environ.get("MERGEPROOF_DB", DATA_DIR / "mergeproof.db"))
SAMPLE_DIR = APP_DIR / "sample_data"
STATIC_DIR = Path(os.environ.get("MERGEPROOF_STATIC", REPO_DIR / "frontend" / "dist"))

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED = 80 * 1024 * 1024
MAX_ROWS_PER_FILE = 20_000
MAX_COLUMNS = 200
MAX_FILES_PER_REQUEST = 12
MAX_SNAPSHOTS_PER_DEAL = 40

ENGINE_VERSION = "mp-recon 1.0.0"
