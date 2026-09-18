"""v2 物理座位上下文：验证 HandContext 并生成引擎索引/角色/盲注适配。

契约：docs/plans/tournament-session/contracts.md（A01/A03/P03/P04/P05）。

- 参与者请求顺序不定义引擎顺序；引擎顺序由物理座位与按钮决定（P05）。
- 两人局：引擎顺序 [BB玩家, BTN/SB玩家]，传原始 (sb, bb)。当前安装的
  PokerKit 在 player_count==2 时反转盲注向量（State._begin_betting 与
  get_effective_blind_or_straddle），该安排经独立回放验证扣盲与行动顺序
  正确（F4），升级依赖后必须保留该行为测试。
- 无小盲（≥3 人）：引擎首位扣大盲，传 raw=(bb, 0)。
- 金额一律非负安全整数；期望值与 BB 换算可为小数但不回写余额。
"""

from __future__ import annotations

import math

from .table import TableError

MAX_SEATS = 9
MIN_SEATS = 2

_RANGE_TABLES = {
    2: ("BB", "BTN"),
    3: ("SB", "BB", "BTN"),
    4: ("SB", "BB", "CO", "BTN"),
    5: ("SB", "BB", "UTG", "CO", "BTN"),
    6: ("SB", "BB", "UTG", "HJ", "CO", "BTN"),
    7: ("SB", "BB", "UTG", "MP", "HJ", "CO", "BTN"),
    8: ("SB", "BB", "UTG", "UTG+1", "MP", "HJ", "CO", "BTN"),
    9: ("SB", "BB", "UTG", "UTG+1", "UTG+2", "MP", "HJ", "CO", "BTN"),
}


def _bad(msg: str) -> TableError:
    return TableError(msg)


def _is_count(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _is_chips(v) -> bool:
    return _is_count(v) and v <= 2 ** 53 - 1


def validate_context(context: dict) -> dict:
    """A03：校验并返回规范化上下文（副本）。非法抛 TableError（→4xx）。"""
    if not isinstance(context, dict):
        raise _bad("context 必须为对象")
    capacity = context.get("capacity")
    if not _is_count(capacity) or not MIN_SEATS <= capacity <= MAX_SEATS:
        raise _bad(f"capacity 须为 {MIN_SEATS}–{MAX_SEATS}")

    participants = context.get("participants")
    if not isinstance(participants, list) or not MIN_SEATS <= len(participants) <= capacity:
        raise _bad(f"participants 须为 2–{capacity} 人")
    seats = []
    occupants = []
    chips_by_occ = {}
    for p in participants:
        if not isinstance(p, dict):
            raise _bad("participants 元素必须为对象")
        seat_id = p.get("seat_id")
        occupant_id = p.get("occupant_id")
        starting = p.get("starting_chips")
        if not _is_count(seat_id) or not 1 <= seat_id <= capacity:
            raise _bad(f"seat_id 非法：{seat_id!r}")
        if seat_id in seats:
            raise _bad(f"seat_id 重复：{seat_id}")
        if not isinstance(occupant_id, str) or not occupant_id:
            raise _bad("occupant_id 必须为非空字符串")
        if occupant_id in occupants:
            raise _bad(f"occupant_id 重复：{occupant_id}")
        if not _is_chips(starting) or starting <= 0:
            raise _bad(f"起始筹码须为正整数：{starting!r}")
        seats.append(seat_id)
        occupants.append(occupant_id)
        chips_by_occ[occupant_id] = starting

    hero = context.get("hero_occupant_id")
    if not isinstance(hero, str) or occupants.count(hero) != 1:
        raise _bad("hero_occupant_id 必须恰好是本手一名参与者")

    blinds = context.get("blinds")
    if not isinstance(blinds, dict):
        raise _bad("blinds 必须为对象")
    sb, bb, ante_each = blinds.get("sb"), blinds.get("bb"), blinds.get("ante_each")
    if not _is_chips(sb) or sb <= 0 or not _is_chips(bb) or bb < sb:
        raise _bad("盲注须为正整数且 bb ≥ sb")
    if not _is_chips(ante_each):
        raise _bad("前注须为非负整数")

    button = context.get("button_seat_id")
    sb_seat = context.get("small_blind_seat_id")
    bb_seat = context.get("big_blind_seat_id")
    if not _is_count(button) or not 1 <= button <= capacity:
        raise _bad(f"button_seat_id 非法：{button!r}")
    seat_set = set(seats)
    if not _is_count(bb_seat) or bb_seat not in seat_set:
        raise _bad("大盲位必须为本手入局者")
    if sb_seat is not None and (not _is_count(sb_seat) or sb_seat not in seat_set):
        raise _bad("小盲位必须为本手入局者或显式为空")

    n = len(seats)
    # 沿物理顺序的自洽校验（P02/P03）
    def clockwise_from(after_seat):
        out = []
        for step in range(1, capacity + 1):
            sid = (after_seat + step - 1) % capacity + 1
            if sid in seat_set:
                out.append(sid)
        return out

    if n == 2:
        if sb_seat != button:
            raise _bad("单挑位置矛盾：BTN 座位同时是小盲位")
        if bb_seat == button:
            raise _bad("单挑位置矛盾：大盲不能在按钮座位")
    elif sb_seat is None:
        if clockwise_from(button)[0] != bb_seat:
            raise _bad("空小盲时大盲必须是庄后首位入局者")
    else:
        order = clockwise_from(button)
        if order[0] != sb_seat or order[1] != bb_seat:
            raise _bad("盲位与物理顺序矛盾（SB 应为庄后首位，BB 次位）")

    icm = context.get("icm") or {}
    scope = icm.get("scope", "off")
    payouts = icm.get("payouts", [])
    if scope not in ("off", "final_table"):
        raise _bad(f"未知 ICM scope：{scope!r}")
    if scope == "final_table":
        if not isinstance(payouts, list) or not payouts or len(payouts) > n:
            raise _bad("ICM 奖金结构无效：名次数须在 1–入局人数")
        for p in payouts:
            if not isinstance(p, (int, float)) or not math.isfinite(p) or p < 0:
                raise _bad(f"ICM 奖金须为非负有限数值：{p!r}")

    learning = context.get("learning_enabled")
    if not isinstance(learning, bool):
        raise _bad("learning_enabled 必须为布尔值")

    profile_names = context.get("profile_names") or {}
    if not isinstance(profile_names, dict):
        raise _bad("profile_names 必须为对象")
    for occ, name in profile_names.items():
        if occ not in occupants:
            raise _bad(f"profile_names 含非本手参与者：{occ!r}")
        if not isinstance(name, str) or not name.strip():
            raise _bad("profile_names 值必须为非空字符串")
    if not learning and profile_names:
        profile_names = {}

    normalized = {
        "session_id": str(context.get("session_id", "")),
        "capacity": capacity,
        "hero_occupant_id": hero,
        "participants": [
            {"seat_id": p["seat_id"], "occupant_id": p["occupant_id"],
             "starting_chips": p["starting_chips"]}
            for p in participants
        ],
        "button_seat_id": button,
        "small_blind_seat_id": sb_seat,
        "big_blind_seat_id": bb_seat,
        "blinds": {"sb": sb, "bb": bb, "ante_each": ante_each},
        "learning_enabled": learning,
        "profile_names": profile_names,
        "icm": {"scope": scope, "payouts": list(payouts)},
    }
    return normalized


def engine_plan(context: dict) -> dict:
    """P05：由已验证上下文生成引擎顺序、索引映射、盲注与前注向量。"""
    capacity = context["capacity"]
    seat_set = {p["seat_id"] for p in context["participants"]}
    info_by_seat = {p["seat_id"]: p for p in context["participants"]}
    button = context["button_seat_id"]
    sb_seat = context["small_blind_seat_id"]
    bb_seat = context["big_blind_seat_id"]
    sb, bb = context["blinds"]["sb"], context["blinds"]["bb"]

    # P05 顺序：从 button 顺时针遇到的入局者；button 有人时最后入列
    order = []
    for step in range(1, capacity):          # 不转回 button 本身
        sid = (button + step - 1) % capacity + 1
        if sid in seat_set:
            order.append(sid)
    if button in seat_set:
        order.append(button)

    n = len(order)
    heads_up = n == 2
    if heads_up:
        # P03：引擎顺序 [BB玩家, BTN/SB玩家]，原始 (sb, bb)（探针验证）
        order = [bb_seat, sb_seat]
        raw_blinds = (sb, bb)
    elif sb_seat is None:
        raw_blinds = (bb, 0)   # 无小盲：首位只扣大盲
    else:
        raw_blinds = (sb, bb)

    index_to_seat = order
    seat_to_index = {sid: i for i, sid in enumerate(order)}

    # P04 range_position：常规 ≥3 人从 SB 顺时针到 BTN；无 SB 用 (n+1) 人表去 SB
    if heads_up:
        range_labels = ("BB", "BTN")
        range_order = [bb_seat, sb_seat]
    elif sb_seat is None:
        if n + 1 > 9 or n == capacity:
            raise _bad("满桌无空位时不能设置无小盲（须现场校准），或人数超出适配范围")
        range_labels = tuple(_RANGE_TABLES[n + 1][1:])
        range_order = _clockwise(seat_set, capacity, bb_seat)
    else:
        range_labels = _RANGE_TABLES[n]
        range_order = _clockwise(seat_set, capacity, sb_seat)
    range_by_index = [
        range_labels[range_order.index(sid)] if sid in range_order else None
        for sid in order
    ]

    roles_by_seat = {sid: [] for sid in order}
    roles_by_seat.setdefault(button, []).append("BTN")
    if sb_seat is not None:
        roles_by_seat.setdefault(sb_seat, []).append("SB")
    roles_by_seat.setdefault(bb_seat, []).append("BB")

    plan = {
        "order": order,
        "index_to_seat": index_to_seat,
        "seat_to_index": seat_to_index,
        "raw_blinds": raw_blinds,
        "antes": context["blinds"]["ante_each"],
        "stacks": [info_by_seat[sid]["starting_chips"] for sid in order],
        "player_count": n,
        "min_bet": bb,
        "big_blind_amount": bb,
        "range_positions": range_by_index,
        "roles_by_seat": {k: v for k, v in roles_by_seat.items() if v},
        "hero_seat_id": next(p["seat_id"] for p in context["participants"]
                             if p["occupant_id"] == context["hero_occupant_id"]),
    }
    return plan


def _clockwise(seat_set, capacity, start):
    out = []
    for step in range(capacity):
        sid = (start - 1 + step) % capacity + 1
        if sid in seat_set:
            out.append(sid)
    return out


def mapping(context: dict, plan: dict) -> list:
    """A01 prepare 返回的 mapping 行。"""
    occ_by_seat = {p["seat_id"]: p["occupant_id"] for p in context["participants"]}
    roles = plan["roles_by_seat"]
    out = []
    for i, sid in enumerate(plan["index_to_seat"]):
        out.append({
            "seat_id": sid,
            "occupant_id": occ_by_seat[sid],
            "engine_index": i,
            "range_position": plan["range_positions"][i],
            "roles": roles.get(sid, []),
        })
    return out


def normalize(context: dict) -> tuple[dict, dict, list]:
    """一步入口：验证 → 规范化 → 引擎计划 → mapping。"""
    normalized = validate_context(context)
    plan = engine_plan(normalized)
    return normalized, plan, mapping(normalized, plan)
