"""Run the review server with isolated statistics; never touch user data."""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backend import opponents
from backend.app import app
import uvicorn

with tempfile.TemporaryDirectory(prefix="poker-glm-review-") as directory:
    opponents._DATA = Path(directory) / "opponents.json"
    opponents._DATA.write_text("{}", encoding="utf-8")
    print(f"ISOLATED_STATS={opponents._DATA}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=8141, log_level="warning")
