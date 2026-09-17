"""第三层集成测试：CFR 均衡参考接入建议链路的端到端行为。"""

from fastapi.testclient import TestClient

from backend.app import app
from backend.decision import advice_for
from backend.table import replay_state

client = TestClient(app)

CFG = {"sb": 100, "bb": 200, "ante": 25}

# 确定性牌堆：该配置+底牌下烧牌固定，以下公共牌均可发（与冒烟验证一致）
FLOP = ["7d", "Ah", "7h"]
TURN, RIVER = "2c", "2d"

BASE = [
    {"op": "action", "type": "fold", "seat": "UTG"},
    {"op": "action", "type": "fold", "seat": "HJ"},
    {"op": "action", "type": "fold", "seat": "CO"},
    {"op": "action", "type": "raise", "to": 600, "seat": "BTN"},
    {"op": "action", "type": "fold", "seat": "SB"},
    {"op": "action", "type": "call", "seat": "BB"},
]


def _river_ops(last_is=("BB", "check")):
    ops = list(BASE) + [
        {"op": "board", "cards": FLOP},
        {"op": "action", "type": "check", "seat": "BB"},
        {"op": "action", "type": "raise", "to": 800, "seat": "BTN"},
        {"op": "action", "type": "call", "seat": "BB"},
        {"op": "board", "cards": [TURN]},
        {"op": "action", "type": "check", "seat": "BB"},
        {"op": "action", "type": "raise", "to": 2100, "seat": "BTN"},
        {"op": "action", "type": "call", "seat": "BB"},
        {"op": "board", "cards": [RIVER]},
    ]
    if last_is:
        ops.append({"op": "action", "type": last_is[1], "seat": last_is[0]})
    return ops


def test_cfr_supported_on_flop_and_turn():
    # 翻牌单挑：BB 过牌后轮到 BTN
    ops = list(BASE) + [{"op": "board", "cards": FLOP},
                        {"op": "action", "type": "check", "seat": "BB"}]
    state, hero_index, _ = replay_state(CFG, "BTN", ["As", "Ad"], ops)
    adv = advice_for(state, hero_index, 3000, seed=7)
    c = adv["cfr"]
    assert c["supported"] is True, c
    assert c["street"] == "翻牌"
    assert c["approximate"] is True
    assert 0 <= c["equity_vs_range"] <= 100
    total = sum(m["freq"] for m in c["mix"])
    assert 0.95 <= total <= 1.001, total

    # 转牌单挑：翻牌两过，发转牌后 BB 再过牌
    ops_turn = ops + [
        {"op": "action", "type": "check", "seat": "BTN"},
        {"op": "board", "cards": [TURN]},
        {"op": "action", "type": "check", "seat": "BB"},
    ]
    state2, hero2, _ = replay_state(CFG, "BTN", ["As", "Ad"], ops_turn)
    adv2 = advice_for(state2, hero2, 3000, seed=7)
    assert adv2["cfr"]["supported"] is True
    assert adv2["cfr"]["street"] == "转牌"
    assert adv2["cfr"]["approximate"] is True


def test_cfr_supported_on_heads_up_river():
    state, hero_index, _ = replay_state(CFG, "BTN", ["As", "Ad"], _river_ops())
    adv = advice_for(state, hero_index, 3000, seed=7)
    c = adv["cfr"]
    assert c["supported"] is True, c
    assert len(c["mix"]) >= 2
    total = sum(m["freq"] for m in c["mix"])
    assert 0.95 <= total <= 1.001, total
    assert all(m["pct"] >= 0 and m["label"] for m in c["mix"])
    assert c["iterations"] >= 200
    assert isinstance(c["agree"], bool)


def test_cfr_unsupported_preflop_and_multiway():
    # 翻前：不是河牌
    state, hero_index, _ = replay_state(CFG, "BTN", ["As", "Ad"], BASE[:3])
    adv = advice_for(state, hero_index, 3000, seed=7)
    assert adv["cfr"]["supported"] is False
    assert "河牌" in adv["cfr"]["reason"]
    # 多人河牌：CO 加注，BTN/BB 跟入，一路 check 到河牌仍是三人底池
    ops = [
        {"op": "action", "type": "fold", "seat": "UTG"},
        {"op": "action", "type": "fold", "seat": "HJ"},
        {"op": "action", "type": "raise", "to": 600, "seat": "CO"},
        {"op": "action", "type": "call", "seat": "BTN"},
        {"op": "action", "type": "fold", "seat": "SB"},
        {"op": "action", "type": "call", "seat": "BB"},
        {"op": "board", "cards": FLOP},
        {"op": "action", "type": "check", "seat": "BB"},
        {"op": "action", "type": "check", "seat": "CO"},
        {"op": "action", "type": "check", "seat": "BTN"},
        {"op": "board", "cards": [TURN]},
        {"op": "action", "type": "check", "seat": "BB"},
        {"op": "action", "type": "check", "seat": "CO"},
        {"op": "action", "type": "check", "seat": "BTN"},
        {"op": "board", "cards": [RIVER]},
        {"op": "action", "type": "check", "seat": "BB"},
    ]
    state2, hero2, _ = replay_state(CFG, "CO", ["As", "Ad"], ops)
    adv2 = advice_for(state2, hero2, 3000, seed=7)
    assert adv2["cfr"]["supported"] is False
    assert "单挑" in adv2["cfr"]["reason"]


def test_cfr_via_api():
    r = client.post("/api/hand/advice", json={
        "config": CFG, "hero_pos": "BTN", "hero_cards": ["As", "Ad"],
        "ops": _river_ops(), "iterations": 3000, "seed": 7,
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert "cfr" in data["advice"]
    assert data["advice"]["cfr"]["supported"] is True
    # 主建议（启发式）不受 CFR 影响，始终存在
    assert data["advice"]["recommendation"]["primary"]
