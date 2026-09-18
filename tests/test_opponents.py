"""第二层：对手建模测试——统计记账、去重、加权采样、统计收窄。"""
import json

import pytest

from backend import opponents
from backend.decision import player_intel
from backend.table import replay_state


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
    state, _, _ = replay_state(cfg, "BTN", ["As", "Ad"], ops)
    intel = player_intel(state)
    r = opponents.record_hand(cfg, ops, {"HJ": "老张"}, intel, hero_index=5)
    assert r["recorded"] is True
    data = opponents._load()
    prof = data["users"]["local"]["named"]["老张"]
    assert prof["hands"] == 1 and prof["vpip"] == 1
    assert data["_pool_pos"]["HJ"]["hands"] == 1    # HJ 平跟计入位置池
    assert data["_pool_pos"]["CO"]["opens"] == 1    # CO 开牌计入位置池
    assert opponents.get_stats("老张")["vpip_pct"] == 100.0


def test_record_dedupes_by_signature(stats_file):
    cfg = {"player_count": 6, "sb": 100, "bb": 200, "ante": 0}
    ops = [{"op": "action", "type": "fold", "seat": "UTG"},
           {"op": "action", "type": "fold", "seat": "HJ"},
           {"op": "action", "type": "fold", "seat": "CO"},
           {"op": "action", "type": "fold", "seat": "BTN"},
           {"op": "action", "type": "fold", "seat": "SB"}]
    lines = {"5": "rfi"}
    state, _, _ = replay_state(cfg, "BTN", ["As", "Ad"], ops)
    intel = player_intel(state)
    r1 = opponents.record_hand(cfg, ops, {"BTN": "老王"}, intel)
    r2 = opponents.record_hand(cfg, ops, {"BTN": "老王"}, intel)
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
        "names": {"UTG": "testn"}, "learning_enabled": True,
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


def test_hand_id_distinguishes_identical_hands(stats_file):
    """14：内容完全相同的两手独立牌局靠 hand_id 区分；同一 hand_id 幂等。"""
    cfg = {"player_count": 6, "sb": 100, "bb": 200, "ante": 0}
    ops = [{"op": "action", "type": "fold", "seat": "UTG"},
           {"op": "action", "type": "fold", "seat": "HJ"},
           {"op": "action", "type": "fold", "seat": "CO"},
           {"op": "action", "type": "fold", "seat": "BTN"},
           {"op": "action", "type": "fold", "seat": "SB"}]
    state, _, _ = replay_state(cfg, "BTN", ["As", "Ad"], ops)
    intel = player_intel(state)
    r1 = opponents.record_hand(cfg, ops, {"BTN": "老王"}, intel, hand_id="h-1")
    r2 = opponents.record_hand(cfg, ops, {"BTN": "老王"}, intel, hand_id="h-2")
    r3 = opponents.record_hand(cfg, ops, {"BTN": "老王"}, intel, hand_id="h-2")
    assert r1["recorded"] is True and r2["recorded"] is True
    assert r3["recorded"] is False
    assert opponents.get_stats("老王")["hands"] == 2


def test_hand_id_via_api(stats_file):
    """14：API 层 hand_id 透传到记录层（内容相同、id 不同都计入）。"""
    from fastapi.testclient import TestClient
    from backend.app import app
    client = TestClient(app)
    cfg = {"player_count": 6, "sb": 100, "bb": 200, "ante": 0}
    ops = [{"op": "action", "type": "fold", "seat": "UTG"},
           {"op": "action", "type": "fold", "seat": "HJ"},
           {"op": "action", "type": "fold", "seat": "CO"},
           {"op": "action", "type": "fold", "seat": "BTN"},
           {"op": "action", "type": "fold", "seat": "SB"}]
    payload = {"config": cfg, "hero_pos": "BTN", "learning_enabled": True,
               "hero_cards": ["As", "Ad"], "ops": ops, "names": {"BTN": "阿强"}}
    for hid in ("api-1", "api-2"):
        payload["hand_id"] = hid
        r = client.post("/api/stats/record", json=payload)
        assert r.status_code == 200
        assert r.json()["recorded"]["recorded"] is True
    payload["hand_id"] = "api-2"
    r = client.post("/api/stats/record", json=payload)
    assert r.json()["recorded"]["recorded"] is False   # 同 id 幂等


def test_user_namespaces_isolate_profiles(stats_file):
    """27：档案按匿名用户 ID 隔离——A 记录的对手 B 看不见；
    reset 只清自己；人群池共享。"""
    cfg = {"player_count": 6, "sb": 100, "bb": 200, "ante": 0}
    ops = [{"op": "action", "type": "fold", "seat": "UTG"},
           {"op": "action", "type": "fold", "seat": "HJ"},
           {"op": "action", "type": "fold", "seat": "CO"},
           {"op": "action", "type": "fold", "seat": "BTN"},
           {"op": "action", "type": "fold", "seat": "SB"}]
    state, _, _ = replay_state(cfg, "BTN", ["As", "Ad"], ops)
    intel = player_intel(state)
    for i in range(10):
        opponents.record_hand(cfg, ops, {}, intel, hand_id=f"seed-{i}", uid="pool-seed")
    opponents.record_hand(cfg, ops, {"BTN": "老王"}, intel, uid="user-A")
    pool_before = opponents.get_pool("BTN")
    assert pool_before is not None and pool_before["hands"] == 11
    assert opponents.get_stats("老王", uid="user-A")["hands"] == 1
    assert opponents.get_stats("老王", uid="user-B")["hands"] == 0
    opponents.reset_user("user-A")
    assert opponents.get_stats("老王", uid="user-A")["hands"] == 0
    assert opponents.get_pool("BTN") == pool_before   # 共享池不受 user reset 影响


def test_hand_id_window_survives_stats_volume(stats_file):
    """29：幂等键独立保存——记录 810 手不同 ID 后重放首个 ID 仍被判重。"""
    cfg = {"player_count": 6, "sb": 100, "bb": 200, "ante": 0}
    ops = [{"op": "action", "type": "fold", "seat": "UTG"},
           {"op": "action", "type": "fold", "seat": "HJ"},
           {"op": "action", "type": "fold", "seat": "CO"},
           {"op": "action", "type": "fold", "seat": "BTN"},
           {"op": "action", "type": "fold", "seat": "SB"}]
    state, _, _ = replay_state(cfg, "BTN", ["As", "Ad"], ops)
    intel = player_intel(state)
    opponents.record_hand(cfg, ops, {}, intel, hand_id="first", uid="u1")
    for i in range(810):
        opponents.record_hand(cfg, ops, {}, intel, hand_id=f"x{i}", uid="u1")
    r = opponents.record_hand(cfg, ops, {}, intel, hand_id="first", uid="u1")
    assert r["recorded"] is False


def test_reserved_name_prefix_falls_back_to_pool(stats_file):
    """31：下划线开头是保留命名空间，归为无名（走人群池），不再 500。"""
    cfg = {"player_count": 6, "sb": 100, "bb": 200, "ante": 0}
    ops = [{"op": "action", "type": "fold", "seat": "UTG"},
           {"op": "action", "type": "fold", "seat": "HJ"},
           {"op": "action", "type": "fold", "seat": "CO"},
           {"op": "action", "type": "fold", "seat": "BTN"},
           {"op": "action", "type": "fold", "seat": "SB"}]
    state, _, _ = replay_state(cfg, "BTN", ["As", "Ad"], ops)
    intel = player_intel(state)
    r = opponents.record_hand(cfg, ops, {"BTN": "_pool"}, intel)
    assert r["recorded"] is True
    data = opponents._load()
    assert "_pool" not in data["users"]["local"]["named"]


def test_migration_consumes_legacy_keys_no_revival(stats_file):
    """39：旧顶层档案迁移进 users.local 后必须删除旧键——否则
    reset_user 清掉 local 后，下次读取又从顶层残留"复活"旧档案。"""
    stats_file.write_text('{"alice": {"hands": 7, "vpip": 2, "pfr": 1, "threebet": 0}}',
                          encoding="utf-8")
    # 迁移幂等：连续两次读取结构一致
    d1 = opponents._load()
    assert d1["schema"] == 2
    assert "alice" not in d1                       # 顶层旧键已消费
    assert opponents.get_stats("alice", uid="local")["hands"] == 7
    opponents.reset_user("local")
    assert opponents.get_stats("alice", uid="local")["hands"] == 0   # 不复活
