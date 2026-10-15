"""本轮审查缺陷的正确行为断言，全部使用隔离统计文件。"""
import json
import threading
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend import equity, opponents


def body():
    return {"protocol_version": 2, "hero_cards": ["As", "Ad"], "ops": [], "context": {
        "session_id": "review", "capacity": 6, "hero_occupant_id": "P5",
        "participants": [{"seat_id": i, "occupant_id": f"P{i}", "starting_chips": 10000}
                         for i in range(1, 7)],
        "button_seat_id": 6, "small_blind_seat_id": 1, "big_blind_seat_id": 2,
        "blinds": {"sb": 100, "bb": 200, "ante_each": 0},
        "learning_enabled": False, "profile_names": {}, "icm": {"scope": "off", "payouts": []}}}


def test_check_cannot_silently_call():
    client = TestClient(app)
    p = body()
    p["ops"] = [{"op": "action", "seat_id": 3, "type": "check"}]
    assert client.post("/api/hand/v2/view", json=p).status_code == 400
    p["ops"][0]["type"] = "call"
    r = client.post("/api/hand/v2/view", json=p)
    assert r.status_code == 200
    assert next(s for s in r.json()["seats"] if s["seat_id"] == 3)["stack"] == 9800


def test_short_allin_is_a_call():
    client = TestClient(app)
    p = body()
    p["context"]["participants"][4]["starting_chips"] = 300
    p["ops"] = [{"op": "action", "seat_id": 3, "type": "raise", "to": 1000},
                {"op": "action", "seat_id": 4, "type": "fold"},
                {"op": "action", "seat_id": 5, "type": "allin"}]
    r = client.post("/api/hand/v2/view", json=p)
    assert r.status_code == 200, r.text
    p["ops"][-1]["type"] = "call"
    assert r.json() == client.post("/api/hand/v2/view", json=p).json()


@pytest.mark.parametrize("payouts", [[1e308, 1e308], [10**400], [0, 0], [-1], [100, 200]])
def test_bad_icm_rejected_consistently(payouts):
    client = TestClient(app, raise_server_exceptions=False)
    p = body()
    p["context"]["icm"] = {"scope": "final_table", "payouts": payouts}
    for path in ("/api/table/prepare", "/api/hand/v2/view", "/api/hand/v2/advice"):
        r = client.post(path, json=p)
        assert r.status_code == 400, (path, r.status_code)


def weighted():
    return {"hero": ["As", "Ah"], "board": ["Kc", "7d", "8h", "9s", "Jc"],
            "villains": [{"type": "combos", "combos": [["Kh", "Kd"], ["2c", "2d"]], "weights": [1, 0]}],
            "iterations": 1000, "seed": 42}


def test_equity_api_preserves_weights():
    p = weighted()
    r = TestClient(app).post("/api/equity", json=p)
    assert r.status_code == 200
    assert r.json()["equity"] == equity.simulate(**p)["equity"] == 0


@pytest.mark.parametrize("weights", [[0, 0], [-1, 2], [1], [1e308, 1e308], [True, 1]])
def test_invalid_weights_rejected(weights):
    p = weighted()
    p["villains"][0]["weights"] = weights
    assert TestClient(app).post("/api/equity", json=p).status_code in (400, 422)


def test_effective_samples_match_scored_trials():
    score = equity._score
    with patch.object(equity, "_score", wraps=score) as scored:
        r = equity.simulate(["2c", "3d"], [{"type": "range", "text": "AKs"}] * 4,
                            ["4h", "5c", "9d", "Ts", "Jh"], 1000, seed=42)
    assert r["iterations"] == scored.call_count // 5
    assert r["requested_iterations"] == 1000
    assert r["rejected_iterations"] == 1000 - r["iterations"]


def record(uid="new", hand_id="one"):
    return opponents.record_hand({"player_count": 6}, [], {},
                                 {str(i): {"kind": "limp"} for i in range(1, 6)},
                                 hero_index=0, hand_id=hand_id, uid=uid)


def test_reset_serializes_with_pending_record(isolated_stats):
    record("old")
    ready, resume, reset_done = threading.Event(), threading.Event(), threading.Event()
    save = opponents._save
    errors = []

    def hold(data):
        if threading.current_thread().name == "pending-record":
            ready.set()
            assert resume.wait(5)
        save(data)

    def run_record():
        try:
            record()
        except Exception as e:
            errors.append(e)

    def reset():
        try:
            opponents.reset_all()
        except Exception as e:
            errors.append(e)
        finally:
            reset_done.set()

    with patch.object(opponents, "_save", side_effect=hold):
        writer = threading.Thread(target=run_record, name="pending-record")
        resetter = threading.Thread(target=reset)
        writer.start()
        try:
            assert ready.wait(5)
            resetter.start()
            assert not reset_done.wait(.1), "reset必须等待已经持锁的记录完成"
        finally:
            resume.set()
            writer.join(5)
            if resetter.ident is not None:
                resetter.join(5)
    assert not errors
    assert json.loads(isolated_stats.read_text(encoding="utf-8")) == {}


@pytest.mark.parametrize("raw", ['{"users":BROKEN', '[]', '{"schema":2,"users":[]}'])
def test_corruption_is_not_overwritten(isolated_stats, raw):
    isolated_stats.write_text(raw, encoding="utf-8")
    with pytest.raises(Exception, match="统计"):
        record()
    assert isolated_stats.read_text(encoding="utf-8") == raw
    r = TestClient(app, raise_server_exceptions=False).get("/api/stats/summary/all")
    assert r.status_code == 503


def test_hand_count_is_not_player_sample_count():
    record("owner")
    record("owner")  # duplicate
    record("other")
    s = opponents.summary("owner")
    assert s["total_hands"] == 1
    assert s["player_samples"] == 10  # shared pool
    assert opponents.get_stats("")["hands"] == 10


def test_forged_uid_cannot_read_or_reset(isolated_stats):
    isolated_stats.write_text(json.dumps({"schema": 2, "users": {"known-owner": {
        "named": {"friend": {"hands": 20}}, "_hand_ids": []}}}), encoding="utf-8")
    attacker = TestClient(app)
    r = attacker.get("/api/stats/friend", headers={"X-Player-Id": "known-owner"})
    assert r.status_code == 200 and r.json()["hands"] == 0
    assert attacker.post("/api/stats/reset", headers={"X-Player-Id": "known-owner"}).status_code == 200
    assert opponents.get_stats("friend", uid="known-owner")["hands"] == 20


def test_cookie_tampering_and_cross_site_writes_rejected():
    client = TestClient(app)
    first = client.get("/api/stats/summary/all")
    assert "httponly" in first.headers.get("set-cookie", "").lower()
    client.cookies.set("poker_session", "known-owner.invalid")
    assert client.get("/api/stats/friend").json()["hands"] == 0
    assert client.post("/api/stats/reset", headers={"Origin": "https://evil.example"}).status_code == 403


def test_identity_key_persists_without_using_repository_data(tmp_path, monkeypatch):
    from backend import browser_identity as auth
    monkeypatch.delenv("POKER_SESSION_SECRET")
    key = tmp_path / "session.key"
    monkeypatch.setenv("POKER_SESSION_KEY_FILE", str(key))
    uid, cookie = auth.identity(None)
    assert len(key.read_bytes()) == 32
    auth._file_key.cache_clear()  # 模拟服务重启读取持久化密钥
    assert auth.identity(cookie) == (uid, None)
    forged = cookie.replace(uid, "anon-" + "a" * 32)
    assert auth.identity(forged)[0] != "anon-" + "a" * 32


def test_reverse_proxy_public_origin_and_secure_cookie(monkeypatch):
    monkeypatch.setenv("POKER_PUBLIC_ORIGIN", "https://ace.example")
    client = TestClient(app)
    r = client.post("/api/stats/reset", headers={"Origin": "https://ace.example"})
    assert r.status_code == 200
    assert "secure" in r.headers["set-cookie"].lower()
    assert client.post("/api/stats/reset", headers={"Origin": "https://other.example"}).status_code == 403


def test_legacy_pool_repaired_and_legacy_hand_count_unknown(isolated_stats):
    isolated_stats.write_text(json.dumps({"schema": 2, "users": {"old": {
        "named": {}, "_hand_ids": ["old-hand"]}}, "_pool": {"hands": 0},
        "_pool_pos": {"UTG": {"hands": 8, "opens": 2, "limps": 1}}}), encoding="utf-8")
    assert opponents.get_stats("")["hands"] == 8
    assert opponents.get_stats("")["vpip_pct"] == 37.5
    assert opponents.summary("old")["total_hands"] is None
    record("old", "new-hand")
    assert opponents.summary("old")["total_hands"] is None
    assert opponents.summary("old")["recorded_hands"] == 1


def test_compute_admission_and_deadline_release_resources(monkeypatch):
    from backend import compute_budget as budget
    from fastapi import HTTPException
    monkeypatch.setattr(budget, "_slots", threading.BoundedSemaphore(1))
    client = TestClient(app)
    with budget.slot():
        r = client.post("/api/equity", json=weighted())
        assert r.status_code == 429 and r.headers["retry-after"] == "2"
        # 轻量局面接口不受计算槽占用影响。
        assert client.post("/api/hand/v2/view", json=body()).status_code == 200
    monkeypatch.setattr(budget, "SECONDS", -1)
    assert client.post("/api/equity", json=weighted()).status_code == 503
    monkeypatch.setattr(budget, "SECONDS", 90)
    assert client.post("/api/equity", json=weighted()).status_code == 200
