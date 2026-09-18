"""Browser acceptance server with temporary, isolated opponent statistics."""
import os
from pathlib import Path
import sys
import tempfile

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend import opponents
from backend.app import app

with tempfile.TemporaryDirectory(prefix="poker-session-qa-") as directory:
    opponents._DATA = Path(directory) / "opponents.json"
    print(f"PID={os.getpid()} ISOLATED_STATS={directory}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("POKER_QA_PORT", "8142")), log_level="warning")
