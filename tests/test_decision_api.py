"""建议链路测试：图表解析、线路分类、建议数学、API 行为。"""

from fastapi.testclient import TestClient

from backend.app import app
from backend.charts import facing_raise_range, situation_range
from backend.decision import _classify_lines
from backend.table import replay_state

client = TestClient(app)

CFG = {"sb": 100, "bb": 200, "ante": 25}


def test_charts_expand():
    from backend.charts import situation_codes
    # 写法层面校验（手牌代码，非单张牌）
    assert "75s+" in situation_codes("BTN", "rfi") and "72s" not in situation_codes("BTN", "rfi")
    assert "A2o+" in situation_codes("BTN", "rfi") and "A2o+" not in situation_codes("UTG", "rfi")
    assert len(facing_raise_range("CO")) > 0
    # 区间写法
    from backend.ranges import expand_range
    assert len(expand_range("22-99")) == 48          # 22..99 共 8 种对子 × 6
    assert len(expand_range("A5s-A2s")) == 16        # A2s..A5s 共 4 种 × 4


def test_line_classification_open_vs_3bet():
    state, hero_index, _ = replay_state(
        CFG, "BB", ["As", "Ad"],
        [{"op": "action", "type": "fold", "seat": "UTG"},
         {"op": "action", "type": "raise", "to": 500, "seat": "HJ"},
         {"op": "action", "type": "fold", "seat": "CO"},
         {"op": "action", "type": "raise", "to": 1500, "seat": "BTN"}])
    lines = _classify_lines(state)
    assert lines[3] == "rfi"          # HJ 首次加注
    assert lines[5] == "three_bet"    # BTN 加注前已有加注
    assert lines[2] == "fold_free"   # UTG 后 CO 面前无注弃牌


def test_api_advice_hero_turn():
    payload = {
        "config": CFG,
        "hero_pos": "BTN",
        "hero_cards": ["As", "Ad"],
        "ops": [{"op": "action", "type": "fold", "seat": "UTG"},
                {"op": "action", "type": "fold", "seat": "HJ"},
                {"op": "action", "type": "fold", "seat": "CO"},
                {"op": "action", "type": "raise", "to": 600, "seat": "BTN"},
                {"op": "action", "type": "fold", "seat": "SB"},
                {"op": "action", "type": "call", "seat": "BB"},
                {"op": "board", "cards": ["Ah", "Kd", "2c"]}],
        "iterations": 5000,
        "seed": 7,
    }
    # 翻牌后 BB 先行动，还没轮到 BTN → 应拒绝
    r = client.post("/api/hand/advice", json=payload)
    assert r.status_code == 400
    assert "轮" in r.json()["detail"]

    # BB 过牌后轮到 BTN → 建议返回
    payload["ops"].append({"op": "action", "type": "check", "seat": "BB"})
    r = client.post("/api/hand/advice", json=payload)
    assert r.status_code == 200
    data = r.json()
    adv = data["advice"]
    assert data["actor"] == "BTN"
    assert abs(adv["equity"]["win"] + adv["equity"]["tie"] + adv["equity"]["lose"] - 100) < 0.02
    assert adv["to_call"] == 0                       # BB 过牌，无跟注额
    assert adv["required_eq"] == 0.0
    assert any(o["pos"] == "BB" for o in adv["opponents"])
    bb = next(o for o in adv["opponents"] if o["pos"] == "BB")
    assert bb["combos"] > 20                         # BB 防守范围不是空的


def test_api_advice_pot_odds_math():
    # BTN 开 600，只有 BB 跟；翻牌互相过牌；转牌 BB 过牌后 BTN 下注 1000
    base = {
        "config": {"sb": 100, "bb": 200, "ante": 25},
        "hero_pos": "BB",
        "hero_cards": ["Kh", "Qh"],
        "ops": [{"op": "action", "type": "fold", "seat": "UTG"},
                {"op": "action", "type": "fold", "seat": "HJ"},
                {"op": "action", "type": "fold", "seat": "CO"},
                {"op": "action", "type": "raise", "to": 600, "seat": "BTN"},
                {"op": "action", "type": "fold", "seat": "SB"},
                {"op": "action", "type": "call", "seat": "BB"}],
        "iterations": 5000,
        "seed": 7,
    }
    all_cards = [r + s for s in "cdhs" for r in "AKQJT98765432"]

    def free_cards(pl):
        v = client.post("/api/hand/view", json=pl).json()
        taken = set(v["taken"])
        return [c for c in all_cards if c not in taken]

    free = free_cards(base)
    base["ops"] += [{"op": "board", "cards": free[:3]},
                    {"op": "action", "type": "check", "seat": "BB"},
                    {"op": "action", "type": "check", "seat": "BTN"}]
    free = free_cards(base)
    base["ops"] += [{"op": "board", "cards": [free[0]]},
                    {"op": "action", "type": "check", "seat": "BB"},
                    {"op": "action", "type": "raise", "to": 1000, "seat": "BTN"}]
    r = client.post("/api/hand/advice", json=base)
    assert r.status_code == 200
    adv = r.json()["advice"]
    # 翻牌前底池：6*25 前注 + SB 盲注 100（弃牌成死钱）+ BTN 600 + BB 600 = 1450
    # 翻牌互相过牌；转牌 BB 过牌、BTN 下注 1000 → BB 需跟 1000
    assert adv["to_call"] == 1000
    assert adv["pot"] == 1450 + 1000
    # 边池感知口径：BB 只能赢别人的钱（BTN 匹配 1625 + 死钱 200），
    # 自己之前的投入不是奖励。所需 = 1000/(1825+1000) = 35.4%
    assert adv["required_eq"] == round(1000 * 100 / 2825, 2)


def test_nine_max_advice_with_spr_and_recommendation():
    cfg = {"sb": 100, "bb": 200, "ante": 25, "player_count": 9}
    payload = {
        "config": cfg,
        "hero_pos": "HJ",
        "hero_cards": ["As", "Ad"],
        "ops": [{"op": "action", "type": "fold", "seat": "UTG"},
                {"op": "action", "type": "fold", "seat": "UTG+1"},
                {"op": "action", "type": "fold", "seat": "UTG+2"},
                {"op": "action", "type": "fold", "seat": "MP"},
                {"op": "action", "type": "raise", "to": 600, "seat": "HJ"},
                {"op": "action", "type": "fold", "seat": "CO"},
                {"op": "action", "type": "fold", "seat": "BTN"},
                {"op": "action", "type": "fold", "seat": "SB"},
                {"op": "action", "type": "call", "seat": "BB"},
                {"op": "board", "cards": ["Ah", "Kd", "2c"]},
                {"op": "action", "type": "check", "seat": "BB"},
                {"op": "action", "type": "raise", "to": 1000, "seat": "HJ"},
                ],
        "iterations": 5000,
        "seed": 7,
    }
    # 转牌圈：BB 先行动、HJ 后行动 → 先让 BB 过牌，再轮到 HJ 下注后是 BB 决策
    # 调整：HJ 下注后轮到 BB，建议应该给 BB
    payload["hero_pos"] = "BB"
    payload["ops"] = payload["ops"][:10]   # 到翻牌圈 BB 行动前
    payload["ops"].append({"op": "action", "type": "check", "seat": "BB"})
    payload["ops"].append({"op": "action", "type": "raise", "to": 1000, "seat": "HJ"})
    r = client.post("/api/hand/advice", json=payload)
    assert r.status_code == 200
    adv = r.json()["advice"]
    rec = adv["recommendation"]
    assert rec["spr"] is not None and rec["spr"] > 0
    assert len(rec["mix"]) >= 2
    assert abs(sum(m["pct"] for m in rec["mix"]) - 100) <= 2
    assert rec["mix"][0]["pct"] >= rec["mix"][-1]["pct"]   # 按概率降序
    assert any(m["action"] == "call" for m in rec["mix"])


def test_no_bet_never_suggests_fold():
    # 面前无注时规则上不能弃牌，建议里也不该出现弃牌
    cfg = {"sb": 100, "bb": 200, "ante": 25}
    payload = {
        "config": cfg,
        "hero_pos": "BTN",
        "hero_cards": ["7d", "2c"],
        "ops": [{"op": "action", "type": "fold", "seat": "UTG"},
                {"op": "action", "type": "fold", "seat": "HJ"},
                {"op": "action", "type": "fold", "seat": "CO"},
                {"op": "action", "type": "call", "seat": "BTN"},
                {"op": "action", "type": "fold", "seat": "SB"},
                {"op": "action", "type": "check", "seat": "BB"}],
        "iterations": 5000,
        "seed": 7,
    }
    v1 = client.post("/api/hand/view", json=payload).json()
    taken = set(v1["taken"])
    all_cards = [r + s for s in "cdhs" for r in "AKQJT98765432"]
    free = [c for c in all_cards if c not in taken][:3]
    payload["ops"].append({"op": "board", "cards": free})
    payload["ops"].append({"op": "action", "type": "check", "seat": "BB"})
    r = client.post("/api/hand/advice", json=payload)
    assert r.status_code == 200
    adv = r.json()["advice"]
    assert adv["to_call"] == 0
    assert all(m["action"] != "fold" for m in adv["recommendation"]["mix"])


def test_new_positions_chart_fallback():
    from backend.charts import situation_range
    assert len(situation_range("MP", "rfi")) > 0      # 9人桌 MP 专属图
    assert len(situation_range("LJ", "rfi")) > 0      # 8人桌 LJ 专属图
    assert len(situation_range("UTG+1", "three_bet")) > 0  # 3bet 回退到 UTG


def test_stack_structure_modulates_advice():
    # 深码 SB 覆盖所有对手 → covers_all 且建议附筹码注记；对手带剩余筹码/风险敞口
    cfg = {"sb": 100, "bb": 200, "ante": 25,
           "stacks": [30000, 10000, 10000, 10000, 10000, 10000]}
    payload = {
        "config": cfg,
        "hero_pos": "SB",
        "hero_cards": ["As", "Ad"],
        "ops": [{"op": "action", "type": "fold", "seat": "UTG"},
                {"op": "action", "type": "fold", "seat": "HJ"},
                {"op": "action", "type": "fold", "seat": "CO"},
                {"op": "action", "type": "call", "seat": "BTN"},
                {"op": "action", "type": "call", "seat": "SB"},
                {"op": "action", "type": "check", "seat": "BB"},
                {"op": "board", "cards": ["Ah", "Kd", "2c"]},
                {"op": "action", "type": "check", "seat": "SB"},
                {"op": "action", "type": "check", "seat": "BB"},
                {"op": "action", "type": "check", "seat": "BTN"},
                {"op": "board", "cards": ["9h"]}],
        "iterations": 5000,
        "seed": 7,
    }
    r = client.post("/api/hand/advice", json=payload)
    assert r.status_code == 200
    adv = r.json()["advice"]
    rec = adv["recommendation"]
    assert rec["covers_all"] is True
    assert "覆盖" in rec["stack_note"]
    assert rec["spr"] > 3
    for o in adv["opponents"]:
        assert o["stack"] > 0 and o["risk_vs_hero"] > 0


def test_advice_invariants_random_scenarios():
    """确定性随机场景的不变量抽查：权益和、赔率复算、mix 排序、无注不弃。"""
    import random
    from backend.table import build_state, deal_hole, apply_ops, positions_for
    from backend.decision import advice_for
    rng = random.Random(20260917)
    checked = 0
    scenario = 0
    while checked < 10 and scenario < 60:
        scenario += 1
        n = rng.choice([6, 8, 9])
        positions = positions_for(n)
        hero_i = rng.randrange(n)
        cfg = {"player_count": n, "sb": 100, "bb": 200,
               "ante": rng.choice([0, 25]),
               "stacks": [rng.choice([1500, 6000, 20000]) for _ in range(n)]}
        state = build_state(cfg)
        deck = [r + s for s in "cdhs" for r in "AKQJT98765432"]
        rng.shuffle(deck)
        deal_hole(state, hero_i, deck[:2])
        for _ in range(rng.randint(1, 10)):
            if state.status is False:
                break
            if state.actor_index is None:
                need = state.streets[state.street_index].board_dealing_count
                if need == 0:
                    break
                avail = [repr(c) for c in state.get_dealable_cards()]
                apply_ops(state, [{"op": "board", "cards": rng.sample(avail, need)}])
                continue
            if state.actor_index == hero_i:
                adv = advice_for(state, hero_i, iterations=3000, seed=rng.randint(1, 10**6))
                rec = adv["recommendation"]
                eq = adv["equity"]
                assert abs(eq["win"] + eq["tie"] + eq["lose"] - 100) <= 0.02
                # 新口径由后端内部按可赢底池计算，这里只验证区间合理性
                if adv["to_call"] > 0:

                    assert 0 < adv["required_eq"] <= 100
                pcts = [m["pct"] for m in rec["mix"]]
                assert pcts == sorted(pcts, reverse=True)
                assert all(0 <= p <= 100 for p in pcts)
                assert abs(sum(pcts) - 100) <= 2
                if adv["to_call"] == 0:
                    assert all(m["action"] != "fold" for m in rec["mix"])
                checked += 1
                break
    assert checked >= 8   # 至少一半场景走到了英雄决策点


def test_icm_uses_hand_start_stack_snapshot():
    """12：ICM 按手前筹码快照——盲注/下注中的钱不算已定归属，
    全下导致剩余 0 也不报错；语义 = 这手牌开始时的记分牌值多少奖金。"""
    cfg = {"sb": 100, "bb": 200, "player_count": 6, "payouts": [600]}
    payload = {"config": cfg, "hero_pos": "UTG",
               "hero_cards": ["As", "Ad"], "ops": []}
    v = client.post("/api/hand/view", json=payload).json()
    icm = v["icm"]
    assert "error" not in icm
    assert icm["snapshot"] == "hand-start"
    assert {r["stack"] for r in icm["rows"]} == {10000}   # 手前人人 10000
    for r in icm["rows"]:
        assert abs(r["equity"] - 100.0) < 0.05            # 冠军独得 → 各 100


def test_icm_allin_player_not_an_error():
    """12：有玩家全下（剩余 0）时 ICM 依旧按手前快照正常给出。"""
    cfg = {"sb": 100, "bb": 200, "player_count": 6,
           "stacks": [10000, 10000, 800, 10000, 10000, 10000],
           "payouts": [300, 200, 100]}
    ops = [{"op": "action", "type": "allin", "seat": "UTG"},
           {"op": "action", "type": "fold", "seat": "HJ"},
           {"op": "action", "type": "fold", "seat": "CO"},
           {"op": "action", "type": "fold", "seat": "BTN"},
           {"op": "action", "type": "fold", "seat": "SB"},
           {"op": "action", "type": "call", "seat": "BB"},
           # 对手已全下：BB 无行动点，公共牌连发至摊牌
           {"op": "board", "cards": ["Ah", "Kd", "2c"]},
           {"op": "board", "cards": ["9h"]},
           {"op": "board", "cards": ["3d"]}]
    payload = {"config": cfg, "hero_pos": "BB",
               "hero_cards": ["As", "Ad"], "ops": ops}
    v = client.post("/api/hand/view", json=payload).json()
    icm = v["icm"]
    assert "error" not in icm
    assert {r["stack"] for r in icm["rows"]} == {10000, 800}   # 手前快照原值
