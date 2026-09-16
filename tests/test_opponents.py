"""第二层：对手建模测试——统计记账、去重、加权采样、统计收窄。"""
import json

import pytest

from backend import opponents


@pytest.fixture
def stats_file(tmp_path, monkeypatch):
    target = tmp_path / "opponents.json"
    monkeypatch.setattr(opponents, "_DATA", target)
    return target


def test_record_accounting(stats_file):
    cfg = {"player_count": 6, "sb": 100, "bb": 200, "ante": 25}
    ops = [
        {"op": "action", "type": "fold", "seat": "UTG"},
        {"op": "action", "type": "call", "seat": "HJ"},
        {"op": "action", "type": "raise", "to": 600, "seat": "CO"},
        {"op": "action", "type": "fold", "seat": "BTN"},
        {"op": "action", "type": "fold", "seat": "SB"},
        {"op": "action", "type": "fold", "seat": "BB"},
    ]
    lines = {"3": "call", "4": "rfi"}   # HJ 平跟、CO 开牌加注
    r = opponents.record_hand(cfg, ops, {"3": "老张"}, lines)
    assert r["recorded"] is True
    data = opponents._load()
    assert data["老张"]["hands"] == 1 and data["老张"]["vpip"] == 1
    assert data["_pool"]["hands"] == 1 and data["_pool"]["pfr"] == 1
    assert opponents.get_stats("老张")["vpip_pct"] == 100.0


def test_record_dedupes_by_signature(stats_file):
    cfg = {"player_count": 6, "sb": 100, "bb": 200, "ante": 0}
    ops = [{"op": "action", "type": "fold", "seat": "UTG"},
           {"op": "action", "type": "fold", "seat": "HJ"},
           {"op": "action", "type": "fold", "seat": "CO"},
           {"op": "action", "type": "fold", "seat": "BTN"},
           {"op": "action", "type": "fold", "seat": "SB"}]
    lines = {"5": "rfi"}
    r1 = opponents.record_hand(cfg, ops, {"BTN": "老王"}, lines)
    r2 = opponents.record_hand(cfg, ops, {"BTN": "老王"}, lines)
    assert r1["recorded"] is True and r2["recorded"] is False
    assert opponents.get_stats("老王")["hands"] == 1


def test_narrowing_flags_in_advice(stats_file):
    # 预置对手档案：100 手只加注 5 次（PFR 5%，低于 UTG 开牌图隐含 ~11%）→ 应触发收窄
    opponents._save({"testn": {"hands": 100, "vpip": 30, "pfr": 5, "threebet": 0}})
    from fastapi.testclient import TestClient
    from backend.app import app
    client = TestClient(app)
    payload = {
        "config": {"player_count": 6, "sb": 100, "bb": 200, "ante": 25},
        "hero_pos": "BB", "hero_cards": ["As", "Ad"],
        "names": {"UTG": "testn"},
        "ops": [{"op": "action", "type": "raise", "to": 600, "seat": "UTG"},
                {"op": "action", "type": "fold", "seat": "HJ"},
                {"op": "action", "type": "fold", "seat": "CO"},
                {"op": "action", "type": "fold", "seat": "BTN"},
                {"op": "action", "type": "fold", "seat": "SB"}],
        "iterations": 5000, "seed": 7,
    }
    r = client.post("/api/hand/advice", json=payload)
    assert r.status_code == 200
    adv = r.json()["advice"]
    utg = next(o for o in adv["opponents"] if o["pos"] == "UTG")
    assert utg["name"] == "testn" and utg["narrowed"] is True
    # 收窄后的范围应明显小于完整开牌图
    from backend.charts import situation_range
    full = len(situation_range("UTG", "rfi"))
    assert 0 < utg["combos"] < full
    assert any(m["action"] == "call" for m in adv["recommendation"]["mix"])


def test_weighted_sampling_shifts_equity():
    from backend.equity import simulate
    hero = ["As", "Ks"]
    strong = [("Ac", "Ad")]           # 对手仅 AA（hero 权益很低）
    weak = [("7d", "6c")]             # 对手 76o（hero 权益很高）
    heavy_weak = [{"type": "combos", "combos": strong + weak, "weights": [1, 99]}]
    heavy_strong = [{"type": "combos", "combos": strong + weak, "weights": [99, 1]}]
    vs_weak = simulate(hero, heavy_weak, [], iterations=8000, seed=5)
    vs_strong = simulate(hero, heavy_strong, [], iterations=8000, seed=5)
    # 对手范围 99% 偏弱牌时 hero 权益显著高于 99% 偏强牌时
    assert vs_weak["win"] - vs_strong["win"] > 30
