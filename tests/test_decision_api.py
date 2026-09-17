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
    # 24：相对弃牌的增量口径——跟注花 1000，争的是跟注后总池 3450
    # （含自己已投入的部分：弃牌即放弃对它的争夺）。1000/3450 = 28.99%
    assert adv["required_eq"] == round(1000 * 100 / 3450, 2)


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
                # 24：恢复独立数学基准——相对弃牌口径的精确赔率
                if adv["to_call"] > 0:
                    assert adv["required_eq"] == round(
                        adv["to_call"] * 100 / (adv["pot"] + adv["to_call"]), 2)
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


def test_blind_up_required_eq_uses_incremental_odds():
    """24：补盲是"相对弃牌"的增量决策——SB 已投 100 在池里，跟 100
    争的是跟注后总池 400，门槛 25%（不是 33.33%）。"""
    cfg = {"sb": 100, "bb": 200, "ante": 0, "player_count": 6}
    payload = {"config": cfg, "hero_pos": "SB",
               "hero_cards": ["7c", "8d"],
               "ops": [{"op": "action", "type": "fold", "seat": "UTG"},
                       {"op": "action", "type": "fold", "seat": "HJ"},
                       {"op": "action", "type": "fold", "seat": "CO"},
                       {"op": "action", "type": "fold", "seat": "BTN"}]}
    r = client.post("/api/hand/advice", json=payload)
    assert r.status_code == 200
    adv = r.json()["advice"]
    assert adv["to_call"] == 100 and adv["pot"] == 300
    assert adv["required_eq"] == 25.0
    assert adv["equity_share"] > 0
    assert adv["side_pots"] and adv["side_pots"][0]["amount"] == 400


def test_cfr_no_raise_when_opponent_allin():
    """21：对手全下（引擎 min/max_raise_to=None）时，CFR 建议不得含加注。"""
    cfg = {"sb": 100, "bb": 200, "ante": 0, "player_count": 6}
    payload = {"config": cfg, "hero_pos": "BTN", "hero_cards": ["As", "Ad"],
               "iterations": 20000,
               "ops": [{"op": "action", "type": "fold", "seat": "UTG"},
                       {"op": "action", "type": "fold", "seat": "HJ"},
                       {"op": "action", "type": "fold", "seat": "CO"},
                       {"op": "action", "type": "call", "seat": "BTN"},
                       {"op": "action", "type": "fold", "seat": "SB"},
                       {"op": "action", "type": "check", "seat": "BB"},
                       {"op": "board", "cards": ["2c", "3d", "7h"]},
                       {"op": "action", "type": "check", "seat": "BB"},
                       {"op": "action", "type": "check", "seat": "BTN"},
                       {"op": "board", "cards": ["9c"]},
                       {"op": "action", "type": "check", "seat": "BB"},
                       {"op": "action", "type": "check", "seat": "BTN"},
                       {"op": "board", "cards": ["Td"]},
                       {"op": "action", "type": "allin", "seat": "BB"}]}
    r = client.post("/api/hand/advice", json=payload)
    assert r.status_code == 200
    adv = r.json()["advice"]
    assert adv["min_raise_to"] is None and adv["max_raise_to"] is None
    if adv["cfr"]["supported"]:
        actions = {m["action"] for m in adv["cfr"]["mix"]}
        assert "raise" not in actions
        assert actions <= {"fold", "call", "check"}


def test_equity_lowercase_range_same_as_upper():
    """20：API 层大小写等价（除耗时字段外逐项一致）。"""
    base = {"hero": ["As", "Ah"], "villains": [{"type": "range", "text": "KK"}],
            "board": ["2c", "3d", "7h", "9c", "Td"],
            "iterations": 2000, "seed": 42}
    r1 = client.post("/api/equity", json=base)
    base["villains"][0]["text"] = "kk"
    r2 = client.post("/api/equity", json=base)
    assert r1.status_code == r2.status_code == 200
    d1, d2 = r1.json(), r2.json()
    d1.pop("elapsedMs"), d2.pop("elapsedMs")
    assert d1 == d2


def test_invalid_names_and_payouts_are_4xx():
    """31：names 值必须为字符串（422）；保留前缀归为无名安全降级；
    非有限奖金 422 而不是 500。"""
    cfg = {"sb": 100, "bb": 200, "ante": 0, "player_count": 6}
    # 全弃短牌谱：手牌即结束，回放不含公共牌维度
    ops = [{"op": "action", "type": "fold", "seat": "UTG"},
           {"op": "action", "type": "fold", "seat": "HJ"},
           {"op": "action", "type": "fold", "seat": "CO"},
           {"op": "action", "type": "fold", "seat": "BTN"},
           {"op": "action", "type": "fold", "seat": "SB"}]
    r = client.post("/api/stats/record", json={"config": cfg, "hero_pos": "BB",
                   "hero_cards": ["As", "Ad"], "ops": ops, "names": {"BB": 123}})
    assert r.status_code == 422
    r2 = client.post("/api/stats/record", json={"config": cfg, "hero_pos": "BB",
                      "hero_cards": ["As", "Ad"], "ops": ops,
                      "names": {"BB": "_seen"}})
    assert r2.status_code == 200       # 保留前缀安全降级为无名
    r3 = client.post("/api/hand/view", json={"config": {
        "sb": 100, "bb": 200, "payouts": ["Infinity", 300]},
        "hero_pos": "BTN", "hero_cards": ["As", "Ad"], "ops": []})
    assert r3.status_code == 422
    r4 = client.post("/api/hand/view", json={"config": {
        "sb": 100, "bb": 200, "payouts": ["NaN", 300]},
        "hero_pos": "BTN", "hero_cards": ["As", "Ad"], "ops": []})
    assert r4.status_code == 422


def test_stats_reset_default_is_self_only():
    """38：reset 的保守默认——不带管理令牌时永远只清自己
    （无论部署是否配置了 POKER_ADMIN_TOKEN）；全清必须持令牌。"""
    import os
    from backend import opponents as opp
    cfg = {"sb": 100, "bb": 200, "ante": 0, "player_count": 6}
    ops = [{"op": "action", "type": "fold", "seat": "UTG"},
           {"op": "action", "type": "fold", "seat": "HJ"},
           {"op": "action", "type": "fold", "seat": "CO"},
           {"op": "action", "type": "fold", "seat": "BTN"},
           {"op": "action", "type": "fold", "seat": "SB"}]
    payload = {"config": cfg, "hero_pos": "BB", "hero_cards": ["As", "Ad"],
               "ops": ops, "names": {"BB": "老张"}}
    old_token = os.environ.pop("POKER_ADMIN_TOKEN", None)
    try:
        # 未配置令牌：远程客户端也只能清自己
        client.post("/api/stats/record", headers={"X-Player-Id": "u-a"}, json=payload)
        r = client.post("/api/stats/reset", headers={"X-Player-Id": "u-a"})
        assert r.json() == {"reset": "user"}, r.json()
        assert client.get("/api/stats/summary/all",
                     headers={"X-Player-Id": "u-a"}).json()["named"] == {}
        # 配置令牌：无令牌仍是自清
        os.environ["POKER_ADMIN_TOKEN"] = "tok-1"
        client.post("/api/stats/record", headers={"X-Player-Id": "u-a"}, json=payload)
        assert client.post("/api/stats/reset",
                      headers={"X-Player-Id": "u-a"}).json() == {"reset": "user"}
        assert client.get("/api/stats/summary/all",
                     headers={"X-Player-Id": "u-a"}).json()["named"] == {}
        # 错误令牌 ≠ 全清
        client.post("/api/stats/record", headers={"X-Player-Id": "u-a"}, json=payload)
        r = client.post("/api/stats/reset",
                   headers={"X-Player-Id": "u-a", "X-Admin-Token": "wrong"})
        assert r.json() == {"reset": "user"}
        assert client.get("/api/stats/summary/all",
                     headers={"X-Player-Id": "u-a"}).json()["named"] == {}
        # 正确令牌 → 全清
        r = client.post("/api/stats/reset",
                   headers={"X-Player-Id": "u-a", "X-Admin-Token": "tok-1"})
        assert r.json() == {"reset": "all"}
    finally:
        if old_token is not None:
            os.environ["POKER_ADMIN_TOKEN"] = old_token


def test_cfr_raise_label_is_street_amount():
    """33：CFR 加注展示必须是本街金额且夹在引擎上下限内，
    不能把全手累计投入当成本街加注到。"""
    cfg = {"sb": 100, "bb": 200, "ante": 0, "player_count": 6,
           "stacks": [10000, 10000, 10000, 10000, 1000, 10000]}
    ops = [{"op": "action", "type": "fold", "seat": "UTG"},
           {"op": "action", "type": "fold", "seat": "HJ"},
           {"op": "action", "type": "fold", "seat": "CO"},
           {"op": "action", "type": "call", "seat": "BTN"},
           {"op": "action", "type": "fold", "seat": "SB"},
           {"op": "action", "type": "check", "seat": "BB"},
           {"op": "board", "cards": ["2c", "3d", "7h"]},
           {"op": "action", "type": "check", "seat": "BB"},
           {"op": "action", "type": "check", "seat": "BTN"},
           {"op": "board", "cards": ["9c"]},
           {"op": "action", "type": "check", "seat": "BB"},
           {"op": "action", "type": "check", "seat": "BTN"},
           {"op": "board", "cards": ["Td"]},
           {"op": "action", "type": "raise", "to": 200, "seat": "BB"}]
    r = client.post("/api/hand/advice", json={"config": cfg, "hero_pos": "BTN",
                    "hero_cards": ["As", "Ad"], "ops": ops, "iterations": 20000})
    assert r.status_code == 200
    adv = r.json()["advice"]
    lo, hi = adv["min_raise_to"], adv["max_raise_to"]
    assert lo is not None and hi is not None
    for m in adv["cfr"]["mix"]:
        if m["action"] == "raise" and "加注到" in m["label"]:
            amt = float(m["label"].replace("加注到 ", "").replace(" BB", "")) * 200
            assert lo - 1e-6 <= amt <= hi + 1e-6, (m["label"], lo, hi)


def test_short_stack_required_eq_uses_contestable_layers():
    """34：短码全下——引擎报 to_call=1000、总池 21300，但英雄只能争
    3300 的可匹配层：门槛 30.30%，而不是 1000/21300=4.69%。"""
    cfg = {"sb": 100, "bb": 200, "ante": 0, "player_count": 6,
           "stacks": [10000, 10000, 10000, 10000, 1000, 10000]}
    ops = [{"op": "action", "type": "allin", "seat": "UTG"},
           {"op": "action", "type": "call", "seat": "HJ"}]
    r = client.post("/api/hand/advice", json={"config": cfg, "hero_pos": "CO",
                    "hero_cards": ["Ks", "Kh"], "ops": ops, "iterations": 20000})
    assert r.status_code == 200
    adv = r.json()["advice"]
    assert adv["required_eq"] == 30.3, adv["required_eq"]
    total = sum(l["amount"] for l in adv["side_pots"])
    assert total == 3300
    assert abs(adv["to_call"] * 100 / total - 30.303) < 0.01


def test_side_pot_ev_drives_recommendation():
    """35：主建议必须与分层 EV 对账——总体权益为 0、主池必输但边池必胜
    （EV=+3000）时，不得以 fold 为主。用分层权益替身做确定性验收。"""
    from unittest.mock import patch
    cfg = {"sb": 100, "bb": 200, "ante": 0, "player_count": 6,
           "stacks": [10000, 10000, 1000, 10000, 10000, 10000]}
    ops = [{"op": "action", "type": "allin", "seat": "UTG"},
           {"op": "action", "type": "call", "seat": "HJ"},
           {"op": "action", "type": "raise", "to": 2000, "seat": "CO"},
           {"op": "action", "type": "fold", "seat": "BTN"},
           {"op": "action", "type": "fold", "seat": "SB"},
           {"op": "action", "type": "fold", "seat": "BB"},
           {"op": "action", "type": "call", "seat": "HJ"},
           {"op": "board", "cards": ["2c", "3d", "7h"]},
           {"op": "action", "type": "check", "seat": "HJ"},
           {"op": "action", "type": "check", "seat": "CO"},
           {"op": "board", "cards": ["9c"]},
           {"op": "action", "type": "check", "seat": "HJ"},
           {"op": "action", "type": "check", "seat": "CO"},
           {"op": "board", "cards": ["Td"]},
           {"op": "action", "type": "raise", "to": 1000, "seat": "HJ"}]
    payload = {"config": cfg, "hero_pos": "CO", "hero_cards": ["Ks", "Kh"],
               "ops": ops, "iterations": 20000}
    # 权益替身：对两个对手（总体）为 0%；单挑 HJ 的边池层为 100%。
    # 期望：主池 3300×0% + 边池 4000×100% - 1000 = +3000，建议以进攻为主。
    def fake_simulate(hero, villains, board, iterations, seed=None):
        base = {"win": 0.0, "tie": 0.0, "lose": 100.0, "equity": 0.0,
                "exact": True, "iterations": iterations, "engine": "mock"}
        if len(villains) == 1:
            return {**base, "equity": 100.0, "win": 100.0, "lose": 0.0}
        return base

    with patch("backend.decision.equity.simulate", side_effect=fake_simulate):
        r = client.post("/api/hand/advice", json=payload)
    assert r.status_code == 200
    adv = r.json()["advice"]
    assert adv["ev_call"] == 3000.0, adv["ev_call"]
    actions = {m["action"]: m["pct"] for m in adv["recommendation"]["mix"]}
    aggressive = sum(p for a, p in actions.items() if a != "fold")
    fold = actions.get("fold", 0)
    assert aggressive > fold, (actions, adv["ev_call"])
