"""牌桌引擎测试：行动顺序、盲注底池、加注校验、边池摊牌。"""

import pytest

from backend.table import POSITIONS, TableError, replay, replay_state

CFG = {"sb": 100, "bb": 200, "ante": 25}


def _new(hero_pos="BTN", cards=("As", "Ad"), ops=None):
    return replay(CFG, hero_pos, list(cards), ops or [])


def test_preflop_pot_with_antes():
    view = _new()
    assert view["pot"] == 25 * 6 + 100 + 200
    assert view["actor"] == "UTG"
    assert view["street"] == "preflop"
    assert view["to_call"] == 200


def test_blinds_and_stacks_after_fold_all():
    # UTG..CO 弃，BTN 全下 10000，SB BB 全弃 → BTN 赢下底池
    ops = [
        {"op": "action", "type": "fold", "seat": "UTG"},
        {"op": "action", "type": "fold", "seat": "HJ"},
        {"op": "action", "type": "fold", "seat": "CO"},
        {"op": "action", "type": "allin", "seat": "BTN"},
        {"op": "action", "type": "fold", "seat": "SB"},
        {"op": "action", "type": "fold", "seat": "BB"},
    ]
    view = replay(CFG, "BTN", ["As", "Ad"], ops)
    assert view["hand_over"] is True
    # BTN 净赢：SB 125(盲注+前注) + BB 225 + UTG/HJ/CO 前注 75（自己的前注互相抵消）
    assert view["payoffs"]["BTN"] == 425


def test_wrong_actor_rejected():
    with pytest.raises(TableError, match="UTG"):
        _new(ops=[{"op": "action", "type": "fold", "seat": "BTN"}])


def test_raise_range_enforced():
    with pytest.raises(TableError, match="不合法"):
        _new(ops=[{"op": "action", "type": "raise", "to": 123, "seat": "UTG"}])
    # 最小加注到 600（2 倍 200 底注 + 200 跟注额）应合法
    view = _new(ops=[{"op": "action", "type": "raise", "to": 600, "seat": "UTG"}])
    assert view["actor"] == "HJ"


def test_postflop_order_sb_first_then_skip_folded():
    ops = [{"op": "action", "type": "call", "seat": "UTG"},   # 平跟 200
           {"op": "action", "type": "fold", "seat": "HJ"},
           {"op": "action", "type": "fold", "seat": "CO"},
           {"op": "action", "type": "fold", "seat": "BTN"},
           {"op": "action", "type": "fold", "seat": "SB"}]    # SB 弃
    view = _new(ops=ops)
    assert view["street"] == "preflop" and view["actor"] == "BB"
    # BB 还没行动前不能发牌；BB 过牌关闭翻牌前，翻牌 BB 先手
    ops2 = ops + [{"op": "board", "cards": ["Ah", "Kd", "2c"]}]
    with pytest.raises(TableError):
        replay(CFG, "BTN", ["As", "Ad"], ops2)
    ops3 = ops + [{"op": "action", "type": "check", "seat": "BB"},
                  {"op": "board", "cards": ["Ah", "Kd", "2c"]}]
    view3 = replay(CFG, "BTN", ["As", "Ad"], ops3)
    assert view3["actor"] == "BB"
    assert view3["board"] == ["Ah", "Kd", "2c"]


def test_board_op_rejected_when_betting_open():
    with pytest.raises(TableError, match="翻牌前"):
        _new(ops=[{"op": "board", "cards": ["Ah", "Kd", "2c"]}])


def test_full_showdown_side_pot_split():
    # BB 短码 1500 全下，BTN(10000) 与 SB(10000) 跟注 → 主池 + 边池
    cfg = {"sb": 100, "bb": 200, "ante": 0,
           "stacks": [10000, 1500, 10000, 10000, 10000, 10000]}
    ops = [
        {"op": "action", "type": "fold", "seat": "UTG"},
        {"op": "action", "type": "fold", "seat": "HJ"},
        {"op": "action", "type": "fold", "seat": "CO"},
        {"op": "action", "type": "call", "seat": "BTN"},     # BTN 跟 200？BTN 在 BTN 位 call 200
        {"op": "action", "type": "call", "seat": "SB"},      # SB 补齐 100
        {"op": "action", "type": "allin", "seat": "BB"},     # BB 全下 1500
        {"op": "action", "type": "allin", "seat": "BTN"},    # BTN 全下
        {"op": "action", "type": "fold", "seat": "SB"},      # SB 弃（损失已投的 200）
        {"op": "board", "cards": ["Ah", "7d", "2c"]},
        {"op": "board", "cards": ["9h"]},
        {"op": "board", "cards": ["5s"]},
    ]
    view = replay(cfg, "BTN", ["As", "Ad"], ops)
    assert view["hand_over"] is True
    total_start = sum(cfg["stacks"])
    total_end = sum(view["seats"][i]["stack"] for i in range(6))
    assert total_start == total_end            # 筹码守恒
    # SB 弃牌净损 200（100 盲注 + 100 平跟补齐）
    assert view["payoffs"]["SB"] == -200
    assert view["payoffs"]["BB"] > 0 or view["payoffs"]["BTN"] > 0  # 必有赢家


def test_showdown_reveals_survivors():
    # BB 全下、BTN 全下跟注打到河牌：摊牌后双方亮牌、弃牌者不亮、盈亏守恒
    cfg = {"sb": 100, "bb": 200, "ante": 25}
    view = replay(cfg, "BTN", ["As", "Ad"], [
        {"op": "action", "type": "fold", "seat": "UTG"},
        {"op": "action", "type": "fold", "seat": "HJ"},
        {"op": "action", "type": "fold", "seat": "CO"},
        {"op": "action", "type": "call", "seat": "BTN"},
        {"op": "action", "type": "call", "seat": "SB"},
        {"op": "action", "type": "allin", "seat": "BB"},
        {"op": "action", "type": "call", "seat": "BTN"},
        {"op": "action", "type": "fold", "seat": "SB"},
        {"op": "board", "cards": ["Ah", "7d", "2c"]},
        {"op": "board", "cards": ["9h"]},
        {"op": "board", "cards": ["5s"]},
    ])
    assert view["hand_over"] is True
    assert "BTN" in view["revealed"] and view["revealed"]["BTN"] == ["As", "Ad"]
    assert "BB" in view["revealed"] and len(view["revealed"]["BB"]) == 2
    assert "UTG" not in view["revealed"] and "SB" not in view["revealed"]
    assert sum(view["payoffs"].values()) == 0
    assert view["payoffs"]["BTN"] > 0          # AA 三条 A 必胜随机牌


def test_all_positions_exist():
    assert POSITIONS == ("SB", "BB", "UTG", "HJ", "CO", "BTN")
    state, hero_index, dealt = replay_state(CFG, "CO", ["Ah", "Ad"], [])
    assert hero_index == 4
    assert len(dealt[hero_index]) == 2


def test_nine_max_positions_and_order():
    cfg = {"sb": 100, "bb": 200, "ante": 25, "player_count": 9}
    v = replay(cfg, "MP", ["As", "Ad"], [])
    assert v["positions"] == ["SB", "BB", "UTG", "UTG+1", "UTG+2", "MP", "HJ", "CO", "BTN"]
    assert v["actor"] == "UTG"
    assert v["pot"] == 25 * 9 + 300          # 9 家前注 + 盲注
    assert len(v["seats"]) == 9


def test_eight_max_fold_around_bb_option():
    cfg = {"sb": 100, "bb": 200, "ante": 0, "player_count": 8}
    ops = [{"op": "action", "type": "fold", "seat": p}
           for p in ("UTG", "UTG+1", "MP", "HJ", "CO")]
    ops.append({"op": "action", "type": "call", "seat": "BTN"})
    ops.append({"op": "action", "type": "fold", "seat": "SB"})
    v = replay(cfg, "BB", ["As", "Ad"], ops)
    assert v["actor"] == "BB"                 # 只剩 BB 有 option
    ops2 = ops + [{"op": "action", "type": "check", "seat": "BB"},
                  {"op": "board", "cards": ["Ah", "Kd", "2c"]}]
    v2 = replay(cfg, "BB", ["As", "Ad"], ops2)
    assert v2["actor"] == "BB"                # 翻牌后 BB 先行动（BTN/SB 已弃）


def test_unknown_table_size_rejected():
    import pytest
    from backend.table import TableError
    with pytest.raises(TableError):
        replay({"sb": 100, "bb": 200, "ante": 0, "player_count": 7}, "BTN", ["As", "Ad"], [])


def test_varied_stacks_accepted_and_shown():
    # 各座位筹码不同：短码 BTN（500）、深码 SB（20000）
    cfg = {"sb": 100, "bb": 200, "ante": 25, "player_count": 6,
           "stacks": [20000, 10000, 10000, 10000, 10000, 500]}
    v = replay(cfg, "BTN", ["As", "Ad"], [])
    stacks = {s["pos"]: s["stack"] for s in v["seats"]}
    # 开局只扣前注25（盲注由引擎在发牌时自动入池）
    assert stacks["BTN"] == 475 and stacks["SB"] == 19875 and stacks["BB"] == 9775
    assert v["pot"] == 25 * 6 + 100 + 200


def test_tiny_stack_trimmed_not_crash():
    # 筹码 15 < 前注 25：前注按筹码修剪（ante trimming），正常开局
    cfg = {"sb": 100, "bb": 200, "ante": 25, "player_count": 6,
           "stacks": [10000, 10000, 10000, 10000, 10000, 15]}
    v = replay(cfg, "BTN", ["As", "Ad"], [])
    btn = next(s for s in v["seats"] if s["pos"] == "BTN")
    assert btn["stack"] == 0
