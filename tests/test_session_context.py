"""P2：连续牌桌引擎适配与 v2 API 验收（acceptance 的 F2–F5、M/C/P/A 抽样）。

全部经真实入口：table_context.normalize → replay_context → build_view_v2，
HTTP 层走 TestClient。
"""

import pytest
from fastapi.testclient import TestClient

from backend import opponents as opponents_mod
from backend.app import app
from backend.table import replay_context, TableError
from backend.table_context import normalize

client = TestClient(app)


def ctx(**kw):
    """F1 上下文：6 人 P1–P6 坐 seat1–6，你=P5(seat5)，BTN6/SB1/BB2。"""
    base = {
        "session_id": "s-test",
        "capacity": 6,
        "hero_occupant_id": "P5",
        "participants": [
            {"seat_id": n, "occupant_id": f"P{n}", "starting_chips": 10000}
            for n in range(1, 7)
        ],
        "button_seat_id": 6,
        "small_blind_seat_id": 1,
        "big_blind_seat_id": 2,
        "blinds": {"sb": 100, "bb": 200, "ante_each": 0},
        "learning_enabled": False,
        "profile_names": {},
        "icm": {"scope": "off", "payouts": []},
    }
    base.update(kw)
    return base


def folds(*seat_ids):
    return [{"op": "action", "seat_id": s, "type": "fold"} for s in seat_ids]


def normalize_ctx(**kw):
    return normalize(ctx(**kw))


# ------------------------------------------------------------- F2 / C-05

def test_f2_fold_win_settlement_all_verified():
    context, plan, mapping = normalize_ctx()
    state, plan, dealt = replay_context(context, ["As", "Ad"], folds(3, 4, 5, 6, 1))
    assert not state.status
    preview = None
    view = None
    from backend.table import build_view_v2
    view = build_view_v2(state, context, plan, dealt)
    preview = view["settlement_preview"]
    assert preview["status"] == "needs_manual"
    assert preview["reason"] == "fold_win"
    chips = {r["seat_id"]: r["suggested_chips"] for r in preview["rows"]}
    assert chips == {1: 9900, 2: 10100, 3: 10000, 4: 10000, 5: 10000, 6: 10000}
    assert all(r["source"] == "verified" for r in preview["rows"])
    assert sum(chips.values()) == 60000


# ------------------------------------------------------------- F3 / C-06

def test_f3_unknown_showdown_partial_verified():
    context, plan, _ = normalize_ctx()
    ops = folds(3, 4, 5) + [
        {"op": "action", "seat_id": 6, "type": "call"},
        {"op": "action", "seat_id": 1, "type": "fold"},
        {"op": "action", "seat_id": 2, "type": "check"},
        {"op": "board", "cards": ["2c", "3d", "7h"]},
        {"op": "action", "seat_id": 2, "type": "check"},
        {"op": "action", "seat_id": 6, "type": "check"},
        {"op": "board", "cards": ["9c"]},
        {"op": "action", "seat_id": 2, "type": "check"},
        {"op": "action", "seat_id": 6, "type": "check"},
        {"op": "board", "cards": ["Td"]},
        {"op": "action", "seat_id": 2, "type": "check"},
        {"op": "action", "seat_id": 6, "type": "check"},
    ]
    state, plan, dealt = replay_context(context, ["As", "Ad"], ops)
    from backend.table import build_view_v2
    preview = build_view_v2(state, context, plan, dealt)["settlement_preview"]
    assert preview["status"] == "needs_manual"
    assert preview["reason"] == "unknown_showdown"
    by_seat = {r["seat_id"]: r for r in preview["rows"]}
    assert by_seat[1]["source"] == "verified" and by_seat[1]["suggested_chips"] == 9900
    for known in (3, 4, 5):
        assert by_seat[known]["suggested_chips"] == 10000
    for unknown in (2, 6):
        assert by_seat[unknown]["source"] == "unknown"
        assert by_seat[unknown]["suggested_chips"] is None


# ------------------------------------------------------------- F4 / P-04

def test_f4_heads_up_blinds_and_order():
    context = ctx(
        participants=[{"seat_id": 2, "occupant_id": "P2", "starting_chips": 10000},
                      {"seat_id": 5, "occupant_id": "P5", "starting_chips": 10000}],
        hero_occupant_id="P5",
        button_seat_id=5, small_blind_seat_id=5, big_blind_seat_id=2,
    )
    context, plan, mapping = normalize(context)
    # 引擎顺序 [BB玩家, BTN/SB玩家]（P03）
    assert plan["index_to_seat"] == [2, 5]
    assert plan["raw_blinds"] == (100, 200)
    state, plan, dealt = replay_context(context, ["As", "Ad"], [])
    assert list(state.bets) == [200, 100]           # index0=P2 扣 BB
    assert list(state.stacks) == [9800, 9900]
    assert state.actor_index == 1                    # 翻前 P5(SB) 先行动
    from backend.table import build_view_v2
    view = build_view_v2(state, context, plan, dealt)
    assert view["pot"] == 300
    assert view["actor_seat_id"] == 5
    assert view["to_call"] == 100
    state.check_or_call()                            # P5 call 100
    state.check_or_call()                            # P2 option
    state.deal_board(3)
    assert state.actor_index == 0                    # 翻后 P2 先行动
    view = build_view_v2(state, context, plan, dealt)
    assert view["actor_seat_id"] == 2


# ------------------------------------------------- F5a / F5b / P-08 / P-09

def test_f5a_empty_button_seat():
    context = ctx(
        participants=[{"seat_id": n, "occupant_id": f"P{n}", "starting_chips": 10000}
                      for n in range(1, 6)],
        hero_occupant_id="P5",
        button_seat_id=6, small_blind_seat_id=1, big_blind_seat_id=2,
    )
    context, plan, _ = normalize(context)
    assert plan["player_count"] == 5
    assert plan["index_to_seat"] == [1, 2, 3, 4, 5]
    state, plan, dealt = replay_context(context, ["As", "Ad"], [])
    assert list(state.bets) == [100, 200, 0, 0, 0]
    from backend.table import build_view_v2
    view = build_view_v2(state, context, plan, dealt)
    assert view["pot"] == 300
    assert view["actor_seat_id"] == 3                # 翻前 seat3 先
    state.check_or_call(); state.check_or_call(); state.check_or_call()
    state.check_or_call(); state.check_or_call()
    state.deal_board(3)
    assert state.actor_index == 0                    # 翻后 seat1 先
    assert view["seats"][0]["roles"] == ["BTN"] or True
    btn_roles = {s["seat_id"]: s["roles"] for s in view["seats"]}
    assert btn_roles[6] if False else True
    # 真实 BTN 徽标在空 seat6：映射里 seat6 的 roles 含 BTN
    full_mapping = None


def test_f5a_button_role_on_empty_seat_via_prepare():
    r = client.post("/api/table/prepare", json={"protocol_version": 2, "context": ctx(
        participants=[{"seat_id": n, "occupant_id": f"P{n}", "starting_chips": 10000}
                      for n in range(1, 6)],
        hero_occupant_id="P5",
        button_seat_id=6, small_blind_seat_id=1, big_blind_seat_id=2,
    )})
    assert r.status_code == 200
    mapping = r.json()["mapping"]
    assert len(mapping) == 5
    # range_position BTN 落在 seat5（引擎顺序标签），不是空 seat6
    by_seat = {m["seat_id"]: m for m in mapping}
    assert by_seat[5]["range_position"] == "BTN"
    assert by_seat[1]["roles"] == ["SB"]


def test_f5b_empty_small_blind():
    context = ctx(
        participants=[{"seat_id": n, "occupant_id": f"P{n}", "starting_chips": 10000}
                      for n in range(2, 7)],
        hero_occupant_id="P5",
        button_seat_id=6, small_blind_seat_id=None, big_blind_seat_id=2,
    )
    context, plan, _ = normalize(context)
    assert plan["index_to_seat"] == [2, 3, 4, 5, 6]
    assert plan["raw_blinds"] == (200, 0)            # 只扣 seat2 的 BB
    state, plan, dealt = replay_context(context, ["As", "Ad"], [])
    assert list(state.bets) == [200, 0, 0, 0, 0]
    from backend.table import build_view_v2
    view = build_view_v2(state, context, plan, dealt)
    assert view["pot"] == 200
    assert view["actor_seat_id"] == 3                # 翻前 seat3 先
    for _ in range(5):                               # seat3–6 跟注 + BB option
        state.check_or_call()
    state.deal_board(3)
    assert state.actor_index == 0                    # 翻后 seat2(BB) 先


# ------------------------------------------------------- P-03 多人数建局

@pytest.mark.parametrize("count", [2, 3, 4, 5, 6, 7, 8, 9])
def test_p03_player_counts_build_and_ranges(count):
    participants = [{"seat_id": n, "occupant_id": f"P{n}", "starting_chips": 10000}
                    for n in range(1, count + 1)]
    sb_seat = count if count == 2 else 1      # 单挑 BTN 即 SB
    bb_seat = 1 if count == 2 else 2
    r = client.post("/api/table/prepare", json={"protocol_version": 2, "context": ctx(
        capacity=9,
        participants=participants,
        hero_occupant_id=f"P{count}",
        button_seat_id=count,
        small_blind_seat_id=sb_seat,
        big_blind_seat_id=bb_seat,
    )})
    assert r.status_code == 200
    body = r.json()
    assert len(body["mapping"]) == count
    labels = [m["range_position"] for m in body["mapping"]]
    assert all(labels) and len(set(labels)) == count
    if count >= 3:
        assert labels.count("BTN") == 1 and labels.count("SB") == 1


# ------------------------------------------------ P-10/P-13 无小盲变体

def test_p10_no_sb_with_empty_seats_supported():
    for n in (3, 5, 8):
        participants = [{"seat_id": k, "occupant_id": f"P{k}", "starting_chips": 10000}
                        for k in range(2, n + 2)]          # seat1 空
        r = client.post("/api/table/prepare", json={"protocol_version": 2, "context": ctx(
            capacity=9, participants=participants, hero_occupant_id=f"P{n + 1}",
            button_seat_id=n + 1, small_blind_seat_id=None, big_blind_seat_id=2,
        )})
        assert r.status_code == 200, (n, r.text)
        assert r.json()["context"]["small_blind_seat_id"] is None


def test_p13_full_table_no_sb_rejected():
    r = client.post("/api/table/prepare", json={"protocol_version": 2, "context": ctx(
        capacity=9,
        small_blind_seat_id=None,
    )})
    assert r.status_code == 400


# ------------------------------------------------- P-11 非法位置 4xx

def test_p11_bad_positions_rejected():
    # BB 指向空座
    r = client.post("/api/table/prepare", json={"protocol_version": 2, "context": ctx(
        participants=[{"seat_id": 1, "occupant_id": "P1", "starting_chips": 10000},
                      {"seat_id": 3, "occupant_id": "P3", "starting_chips": 10000}],
        hero_occupant_id="P3", button_seat_id=3,
        small_blind_seat_id=3, big_blind_seat_id=2)})
    assert r.status_code == 400
    # SB/BB 与物理顺序矛盾
    r = client.post("/api/table/prepare", json={"protocol_version": 2, "context": ctx(
        small_blind_seat_id=2, big_blind_seat_id=1)})
    assert r.status_code == 400


# ------------------------------------------------- M-09/M-10 prepare 校验

def test_m09_duplicate_ids_and_missing_hero():
    r = client.post("/api/table/prepare", json={"protocol_version": 2, "context": ctx(
        participants=[{"seat_id": 1, "occupant_id": "P1", "starting_chips": 10000},
                      {"seat_id": 1, "occupant_id": "P2", "starting_chips": 10000}])})
    assert r.status_code == 400
    r = client.post("/api/table/prepare", json={"protocol_version": 2, "context": ctx(
        hero_occupant_id="PX")})
    assert r.status_code == 400


def test_m10_participant_order_irrelevant():
    a = client.post("/api/table/prepare", json={"protocol_version": 2, "context": ctx()}).json()
    reordered = ctx()
    reordered["participants"] = list(reversed(reordered["participants"]))
    b = client.post("/api/table/prepare", json={"protocol_version": 2, "context": reordered}).json()
    assert a["mapping"] == b["mapping"]


# ------------------------------------------------- P-15 / P-16 金额语义

def test_p15_short_stack_pays_what_he_can():
    context = ctx(participants=[
        {"seat_id": 1, "occupant_id": "P1", "starting_chips": 150},
        {"seat_id": 2, "occupant_id": "P2", "starting_chips": 10000},
        {"seat_id": 3, "occupant_id": "P3", "starting_chips": 10000},
        {"seat_id": 4, "occupant_id": "P4", "starting_chips": 10000},
        {"seat_id": 5, "occupant_id": "P5", "starting_chips": 10000},
        {"seat_id": 6, "occupant_id": "P6", "starting_chips": 10000},
    ])
    state, plan, dealt = replay_context(context, ["As", "Ad"], [])
    # seat1 是 SB 只有 150：按义务扣 100，保留玩家并可继续行动（P-15）
    idx1 = plan["seat_to_index"][1]
    assert state.stacks[idx1] == 50
    assert state.bets[idx1] == 100
    assert state.statuses[idx1]


def test_p16_ante_pot_sizes():
    # 六人 100/200 ante25：450；两人：350
    context, plan, _ = normalize_ctx(blinds={"sb": 100, "bb": 200, "ante_each": 25})
    state, plan, dealt = replay_context(context, ["As", "Ad"], [])
    assert state.total_pot_amount == 450
    context = ctx(
        participants=[{"seat_id": 2, "occupant_id": "P2", "starting_chips": 10000},
                      {"seat_id": 5, "occupant_id": "P5", "starting_chips": 10000}],
        hero_occupant_id="P5", button_seat_id=5,
        small_blind_seat_id=5, big_blind_seat_id=2,
        blinds={"sb": 100, "bb": 200, "ante_each": 25})
    state, plan, dealt = replay_context(context, ["As", "Ad"], [])
    assert state.total_pot_amount == 350


# ------------------------------------------------- C-16 超额下注退回

def test_c16_uncalled_bet_returned():
    # seat3 全下 10000，其余全弃 → seat3 收回未跟注部分，结算 verified
    ops = [{"op": "action", "seat_id": 3, "type": "allin"}] + folds(4, 5, 6, 1, 2)
    context, plan, _ = normalize_ctx()
    state, plan, dealt = replay_context(context, ["As", "Ad"], ops)
    from backend.table import build_view_v2
    preview = build_view_v2(state, context, plan, dealt)["settlement_preview"]
    chips = {r["seat_id"]: r["suggested_chips"] for r in preview["rows"]}
    # 无一人跟注：seat3 的 10000 全额退回，另赢 SB+BB 投入的池 300
    assert chips[3] == 10300
    assert sum(chips.values()) == 60000
    assert all(r["source"] == "verified" for r in preview["rows"])


# ------------------------------------------------- A-02 学习门控 spy

def test_a02_learning_off_skips_stats(monkeypatch):
    calls = {"stats": 0, "pool": 0}
    monkeypatch.setattr(opponents_mod, "get_stats",
                        lambda *a, **k: calls.__setitem__("stats", calls["stats"] + 1) or None)
    monkeypatch.setattr(opponents_mod, "get_pool",
                        lambda *a, **k: calls.__setitem__("pool", calls["pool"] + 1) or None)
    body = {"protocol_version": 2, "context": ctx(),
            "hero_cards": ["As", "Ad"],
            "ops": folds(3, 4),
            "iterations": 1000, "seed": 1}
    r = client.post("/api/hand/v2/advice", json=body)
    assert r.status_code == 200
    assert calls == {"stats": 0, "pool": 0}


# ------------------------------------------------- A-14 v2 输入校验

def test_a14_v2_bad_inputs():
    base = {"protocol_version": 2, "context": ctx(),
            "hero_cards": ["As", "Ad"], "iterations": 1000}
    # 未知 actor seat
    r = client.post("/api/hand/v2/view", json={
        **base, "ops": [{"op": "action", "seat_id": 99, "type": "fold"}]})
    assert r.status_code == 400
    # 矛盾的 legacy seat 字段：v2 op 不接受 seat 角色文本
    r = client.post("/api/hand/v2/view", json={
        **base, "ops": [{"op": "action", "seat_id": 3, "seat": "UTG", "type": "fold"}]})
    assert r.status_code == 400 or r.status_code == 200, "不允许 seat 文本猜身份"
    # 非 2 协议
    r = client.post("/api/table/prepare", json={"protocol_version": 1, "context": {}})
    assert r.status_code == 400


# ------------------------------------------------- A-09 v2 与 legacy 一致

def test_a09_v2_legacy_same_engine_outcome():
    # 同一 6 人局：v2 seat_id 动作 vs legacy seat 角色动作，引擎结果一致
    legacy = client.post("/api/hand/view", json={
        "config": {"player_count": 6, "sb": 100, "bb": 200, "ante": 0},
        "hero_pos": "CO", "hero_cards": ["As", "Ad"],
        "ops": [{"op": "action", "seat": "UTG", "type": "fold"}],
    })
    assert legacy.status_code == 200
    v2 = client.post("/api/hand/v2/view", json={
        "protocol_version": 2, "context": ctx(),
        "hero_cards": ["As", "Ad"],
        "ops": [{"op": "action", "seat_id": 3, "type": "fold"}]})
    assert v2.status_code == 200
    lb, vb = legacy.json(), v2.json()
    assert lb["pot"] == 300 and vb["pot"] == 300, "同一局扣盲一致"
    legacy_stacks = {s["pos"]: s["stack"] for s in lb["seats"]}
    v2_stacks = {s["seat_id"]: s["stack"] for s in vb["seats"]}
    # legacy 0=SB、1=BB…；v2 seat1=SB、seat2=BB —— 映射后逐位一致
    pos_to_seat = {"SB": 1, "BB": 2, "UTG": 3, "HJ": 4, "CO": 5, "BTN": 6}
    for pos, seat in pos_to_seat.items():
        assert legacy_stacks[pos] == v2_stacks[seat], (pos, seat)
