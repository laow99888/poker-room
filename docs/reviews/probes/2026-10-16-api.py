"""Independent review probes. Writes only to temporary opponent storage.

Run from the repository root: python docs/reviews/probes/2026-10-16-api.py
Assertions demonstrate the CURRENT defects, not passing acceptance criteria.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fastapi.testclient import TestClient
from backend.app import app
from backend import equity, opponents

ROOT = Path(__file__).resolve().parents[3]
RESULTS = []


def context():
    return {
        "session_id": "isolated-review", "capacity": 6,
        "hero_occupant_id": "P5",
        "participants": [{"seat_id": i, "occupant_id": f"P{i}", "starting_chips": 10000}
                         for i in range(1, 7)],
        "button_seat_id": 6, "small_blind_seat_id": 1, "big_blind_seat_id": 2,
        "blinds": {"sb": 100, "bb": 200, "ante_each": 0},
        "learning_enabled": False, "profile_names": {},
        "icm": {"scope": "off", "payouts": []},
    }


def body():
    return {"protocol_version": 2, "context": context(),
            "hero_cards": ["As", "Ad"], "ops": []}


def record(name, **detail):
    RESULTS.append({"name": name, "reproduced": True, "detail": detail})
    print(json.dumps(RESULTS[-1], ensure_ascii=True), flush=True)


with tempfile.TemporaryDirectory(prefix="poker-review-api-") as directory:
    target = Path(directory) / "opponents.json"
    with patch.object(opponents, "_DATA", target):
        client = TestClient(app, raise_server_exceptions=False)
        p = body()
        p["ops"] = [{"op": "action", "seat_id": 3, "type": "check"}]
        r = client.post("/api/hand/v2/view", json=p)
        assert r.status_code == 200
        seat = next(s for s in r.json()["seats"] if s["seat_id"] == 3)
        assert seat["stack"] == 9800
        record("check-silently-calls", status=r.status_code,
               requested="check", starting=10000, resulting=seat["stack"], pot=r.json()["pot"])

        p = body()
        p["context"]["participants"][4]["starting_chips"] = 300
        prefix = [{"op": "action", "seat_id": 3, "type": "raise", "to": 1000},
                  {"op": "action", "seat_id": 4, "type": "fold"}]
        responses = {}
        for action in ("allin", "call"):
            p["ops"] = prefix + [{"op": "action", "seat_id": 5, "type": action}]
            responses[action] = client.post("/api/hand/v2/view", json=p).status_code
        assert responses == {"allin": 400, "call": 200}
        record("short-allin-call-rejected", **responses)

        p = body()
        p["context"]["icm"] = {"scope": "final_table", "payouts": [1e308, 1e308]}
        prepare = client.post("/api/table/prepare", json=p)
        view = client.post("/api/hand/v2/view", json=p)
        assert prepare.status_code == 200 and view.status_code == 500
        p["context"]["icm"]["payouts"] = [10 ** 400]
        huge = client.post("/api/table/prepare", json=p)
        assert huge.status_code == 500
        record("icm-overflow-500", finite_sum_prepare=prepare.status_code,
               finite_sum_view=view.status_code, huge_integer_prepare=huge.status_code)

        p = {"hero": ["As", "Ah"],
             "villains": [{"type": "combos", "combos": [["Kh", "Kd"], ["2c", "2d"]], "weights": [1, 0]}],
             "board": ["Kc", "7d", "8h", "9s", "Jc"], "iterations": 1000, "seed": 42}
        api = client.post("/api/equity", json=p).json()
        direct = equity.simulate(**p)
        assert direct["equity"] == 0 and api["equity"] > 40
        record("api-drops-weights", api_equity=api["equity"], direct_equity=direct["equity"])

        original = equity._score
        calls = 0

        def count(cards):
            global calls
            calls += 1
            return original(cards)

        with patch.object(equity, "_score", side_effect=count):
            r = equity.simulate(["2c", "3d"], [{"type": "range", "text": "AKs"}] * 4,
                                ["4h", "5c", "9d", "Ts", "Jh"], 1000, seed=42)
        assert calls // 5 < r["iterations"]
        record("requested-vs-effective-samples", reported=r["iterations"], effective=calls // 5)

        # Pause a record after it has read the old data, while holding its normal lock.
        # reset_all does not take that lock, so its cleared store is overwritten.
        target.write_text(json.dumps({"schema": 2, "users": {"old": {"named": {"history": {"hands": 7}}, "_hand_ids": []}}}), encoding="utf-8")
        old_save = opponents._save
        ready, resume = threading.Event(), threading.Event()
        errors = []

        def hold_save(data):
            if threading.current_thread().name == "review-record":
                ready.set()
                if not resume.wait(5):
                    raise RuntimeError("probe synchronisation timeout")
            old_save(data)

        def recording():
            try:
                opponents.record_hand({"player_count": 6}, [], {}, {"1": {"kind": "limp"}},
                                      hero_index=0, hand_id="review-concurrent", uid="new")
            except Exception as exc:
                errors.append(repr(exc))

        with patch.object(opponents, "_save", side_effect=hold_save):
            thread = threading.Thread(target=recording, name="review-record")
            thread.start()
            try:
                assert ready.wait(5)
                opponents.reset_all()
                assert json.loads(target.read_text(encoding="utf-8")) == {}
            finally:
                resume.set()
                thread.join(5)
            assert not thread.is_alive() and not errors
        data = json.loads(target.read_text(encoding="utf-8"))
        assert "old" in data["users"]
        record("global-reset-race", reset_returned_success=True,
               deleted_user_restored="old" in data["users"])

        target.write_text('{"users":BROKEN', encoding="utf-8")
        opponents.record_hand({"player_count": 6}, [], {}, {"1": {"kind": "limp"}},
                              hero_index=0, hand_id="review-after-corruption", uid="new")
        assert "BROKEN" not in target.read_text(encoding="utf-8")
        assert not list(target.parent.glob("*.bak"))
        record("corrupt-stats-overwritten", backup_created=False)

        opponents.reset_all()
        opponents.record_hand({"player_count": 6}, [], {},
                              {str(i): {"kind": "limp"} for i in range(1, 6)},
                              hero_index=0, hand_id="review-one-hand", uid="review")
        summary = opponents.summary("review")
        assert summary["total_hands"] == 5
        record("summary-count-is-player-samples", completed_hands=1,
               total_hands=summary["total_hands"], legacy_pool_hands=opponents._load()["_pool"]["hands"])

        target.write_text(json.dumps({"schema": 2, "users": {"known-owner": {
            "named": {"friend": {"hands": 20, "vpip": 5, "pfr": 3, "threebet": 1}}, "_hand_ids": []}}}), encoding="utf-8")
        forged = {"X-Player-Id": "known-owner"}
        visible = client.get("/api/stats/friend", headers=forged)
        assert visible.status_code == 200 and visible.json()["hands"] == 20
        cleared = client.post("/api/stats/reset", headers=forged)
        assert cleared.status_code == 200 and cleared.json()["reset"] == "user"
        assert client.get("/api/stats/friend", headers=forged).json()["hands"] == 0
        record("forged-owner-can-read-and-reset", authenticated=False,
               attacker_knows_uid=True, read_hands=20, hands_after_reset=0)

out = ROOT / "docs/screenshots/review-2026-10-16/api-results.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({"results": RESULTS}, ensure_ascii=False, indent=2), encoding="utf-8")
