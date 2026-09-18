"""无限注德州扑克牌桌引擎封装（基于 PokerKit）。

座位模型：内部索引 0 = 小盲，1 = 大盲，末位 = 按钮（BTN），中间座位
按离大盲的远近命名，随桌型变化：

    6 人: SB, BB, UTG, HJ, CO, BTN
    8 人: SB, BB, UTG, UTG+1, MP, HJ, CO, BTN
    9 人: SB, BB, UTG, UTG+1, UTG+2, MP, HJ, CO, BTN

发牌从 SB 开始一圈两轮；翻前 UTG 先行动，翻后从按钮左侧第一个
未弃牌玩家开始。所有机械步骤（前注/盲注/收注/烧牌/推筹/摊牌展示）
由 PokerKit 自动化完成，本模块只回放人类决策与公共牌。
"""

import random
from typing import Optional

from pokerkit import Automation, Card as PKCard, NoLimitTexasHoldem

TABLE_SIZES = (6, 8, 9)
_POSITIONS_BY_SIZE = {
    6: ("SB", "BB", "UTG", "HJ", "CO", "BTN"),
    8: ("SB", "BB", "UTG", "UTG+1", "MP", "HJ", "CO", "BTN"),
    9: ("SB", "BB", "UTG", "UTG+1", "UTG+2", "MP", "HJ", "CO", "BTN"),
}
POSITIONS = _POSITIONS_BY_SIZE[6]  # 兼容旧引用（默认 6 人桌）
STREETS = ("preflop", "flop", "turn", "river")


def positions_for(player_count: int) -> tuple:
    """按桌型返回座位名元组（0=SB，1=BB，末位=BTN）。"""
    if player_count not in _POSITIONS_BY_SIZE:
        raise TableError(f"桌型仅支持 {list(_POSITIONS_BY_SIZE)} 人桌")
    return _POSITIONS_BY_SIZE[player_count]

_AUTOMATIONS = (
    Automation.ANTE_POSTING,
    Automation.BET_COLLECTION,
    Automation.BLIND_OR_STRADDLE_POSTING,
    Automation.CARD_BURNING,
    Automation.CHIPS_PUSHING,
    Automation.CHIPS_PULLING,
    Automation.HOLE_CARDS_SHOWING_OR_MUCKING,
    Automation.HAND_KILLING,
)

_FILLER_SEED = 20260916  # 占位牌固定种子，保证同一手牌回放完全一致

_ALL_CARDS = [r + s for s in "cdhs" for r in "AKQJT98765432"]


class TableError(ValueError):
    """非法配置或非法操作（含步骤序号，方便前端定位）。"""


def _parse_card(text: str, used: set) -> str:
    t = str(text).strip()
    if len(t) == 3 and t[1].upper() == "1":
        t = "T" + t[2]
    if len(t) != 2 or t[0].upper() not in "AKQJT98765432" or t[1].lower() not in "cdhs":
        raise TableError(f"无效的牌：{text!r}（示例：As、Kh、Td）")
    card = t[0].upper() + t[1].lower()
    if card in used:
        raise TableError(f"牌 {card} 已经在牌局中（重复）")
    used.add(card)
    return card


def build_state(config: dict) -> "NoLimitTexasHoldem":
    """按桌型/盲注/前注/筹码配置创建牌局。"""
    try:
        sb, bb, ante = int(config["sb"]), int(config["bb"]), int(config.get("ante", 0))
        player_count = int(config.get("player_count", 6))
        stacks = config.get("stacks")
    except (KeyError, TypeError, ValueError) as exc:
        raise TableError(f"配置不完整或非法：{exc}") from exc
    if player_count not in _POSITIONS_BY_SIZE:
        raise TableError(f"桌型仅支持 {list(_POSITIONS_BY_SIZE)} 人桌")
    if not 0 < sb <= bb:
        raise TableError("需要 0 < 小盲 ≤ 大盲")
    if ante < 0:
        raise TableError("前注不能为负")
    if stacks is None:
        stacks = [bb * 50] * player_count
    if len(stacks) != player_count or any(int(x) <= 0 for x in stacks):
        raise TableError(f"筹码须为 {player_count} 个正整数")
    # PokerKit 的模拟烧牌在发牌/发公共牌时消费全局 random。随机性必须由
    # replay_state 统一固定（否则同一手牌两次回放烧牌序列不同，view/advice
    # 会漂移）；这里不再自行固定种子。
    try:
        return _create_engine_state(player_count, sb, bb, ante, stacks, bb)
    except ValueError as exc:
        raise TableError(f"筹码设置无法开局（检查是否有座位付不起盲注/前注）：{exc}") from exc


def _create_engine_state(player_count, first_blind, second_blind, ante,
                         stacks, min_bet):
    """引擎状态创建（P05 适配层专用）。

    first/second_blind 是传给 PokerKit 的原始盲注向量：常规 (sb, bb)；
    两人局 (sb, bb) 配 [BB, BTN/SB] 顺序（PokerKit 会反转向量）；
    无小盲 (bb, 0)。player_count 已由调用方验证（legacy 6/8/9 或 v2 2–9）。
    """
    return NoLimitTexasHoldem.create_state(
        automations=_AUTOMATIONS,
        ante_trimming_status=True,
        raw_antes=ante,
        raw_blinds_or_straddles=(first_blind, second_blind),
        min_bet=min_bet,
        raw_starting_stacks=[int(x) for x in stacks],
        player_count=player_count,
    )


def deal_hole(state, hero_index: int, hero_cards, reserve=()) -> dict:
    """发底牌：英雄拿真实手牌，其余座位发确定性占位牌。

    reserve：操作序列里声明的公共牌——占位牌必须避开，否则真实牌谱
    会被模拟占位锁死（占位牌只是驱动状态机的虚构牌，不是观察事实）。
    返回 {座位索引: [牌, 牌]}（英雄为真实牌，其余为占位）。
    """
    used = set()
    hero_cards = [_parse_card(c, used) for c in hero_cards]
    if len(hero_cards) != 2:
        raise TableError("底牌必须是 2 张")
    declared_board = set()
    for c in reserve:
        declared_board.add(_parse_card(c, used))
    n = state.player_count
    dealt = {i: [] for i in range(n)}
    fillers = [c for c in _ALL_CARDS if c not in used]
    random.Random(_FILLER_SEED).shuffle(fillers)
    needed = 2 * n - len(hero_cards)
    if len(fillers) < needed:
        raise TableError("声明的公共牌过多，剩余牌不够发占位牌")
    order = [seat for _round in range(2) for seat in range(n)]  # SB→BTN 两圈
    for seat in order:
        if seat == hero_index:
            card = hero_cards.pop(0) if hero_cards else fillers.pop(0)
        else:
            card = fillers.pop(0)
        state.deal_hole(PKCard(card[0], card[1]))
        dealt[seat].append(card)
    return dealt


def apply_ops(state, ops, dealt=None, hero_index=None) -> None:
    """按序回放操作（行动 / 发公共牌），任何非法步骤抛 TableError。"""
    for idx, op in enumerate(ops, start=1):
        kind = op.get("op")
        try:
            if kind == "action":
                _apply_action(state, op)
            elif kind == "board":
                _apply_board(state, op, dealt, hero_index)
            else:
                raise TableError(f"未知操作类型：{kind!r}")
        except TableError:
            raise
        except ValueError as exc:
            raise TableError(f"第 {idx} 步无效：{exc}") from exc


def _apply_action(state, op) -> None:
    positions = positions_for(state.player_count)
    actor = state.actor_index
    if actor is None:
        raise TableError("当前没有待行动玩家（可能该发公共牌了）")
    seat = op.get("seat")
    seat_index = op.get("seat_index")
    if seat_index is not None:
        # v2：直接用引擎索引（由 seat_id 映射而来）
        if not isinstance(seat_index, int) or not 0 <= seat_index < state.player_count:
            raise TableError(f"引擎座位索引非法：{seat_index!r}")
        if seat_index != actor:
            raise TableError(f"轮到 {positions[actor]} 行动，不是该座位")
    elif seat is not None:
        if seat not in positions:
            raise TableError(f"未知座位：{seat!r}")
        if positions.index(seat) != actor:
            raise TableError(f"轮到 {positions[actor]} 行动，不是 {seat}")

    kind = op.get("type")
    if kind == "fold":
        state.fold()
    elif kind in ("check", "call"):
        state.check_or_call()
    elif kind == "raise":
        to = op.get("to")
        if to is None:
            raise TableError("加注需要 to（加注到多少）")
        try:
            to = int(to)
        except (TypeError, ValueError) as exc:
            raise TableError(f"加注数额非法：{to!r}") from exc
        lo = state.min_completion_betting_or_raising_to_amount
        hi = state.max_completion_betting_or_raising_to_amount
        if lo is None or hi is None:
            raise TableError(f"{positions[actor]} 当前没有加注权（面对全下只能跟注/弃牌）")
        if not lo <= to <= hi:
            hint = "（此数额即全下）" if to == hi else ""
            raise TableError(
                f"{positions[actor]} 加注到 {to} 不合法，"
                f"范围 [{lo}, {hi}]{hint}"
            )
        state.complete_bet_or_raise_to(to)
    elif kind == "allin":
        state.complete_bet_or_raise_to(state.max_completion_betting_or_raising_to_amount)
    else:
        raise TableError(f"未知动作：{kind!r}")


def _apply_board(state, op, dealt=None, hero_index=None) -> None:
    cards = op.get("cards")
    if not isinstance(cards, list) or not cards:
        raise TableError("发牌操作需要 cards 列表")
    street = STREETS[state.street_index]
    expect = state.streets[state.street_index].board_dealing_count
    if expect == 0:
        raise TableError("当前还在翻牌前，下注结束后才能发公共牌")
    if len(cards) != expect:
        raise TableError(f"{street} 应一次发 {expect} 张，收到 {len(cards)} 张")
    # 真实观察到的公共牌只需与"任何人底牌 + 已发公共牌"不冲突。
    # 占位牌已在回放开始时避开声明的公共牌；撞上模拟烧牌时报清晰错误。
    occupied = set()
    if dealt:
        for cards_i in dealt.values():
            occupied.update(cards_i)
    occupied.update(repr(c) for street_i in state.board_cards for c in street_i)
    clean = []
    for c in cards:
        code = repr(c).strip() if not isinstance(c, str) else str(c).strip()
        if code in occupied:
            raise TableError(
                f"牌 {code} 已在牌局中（某人底牌或已发出的公共牌），不能再次发出")
        clean.append(PKCard(code[0], code[1]))
    try:
        state.deal_board(tuple(clean))
    except ValueError as exc:
        raise TableError(
            f"公共牌 {cards} 与模拟发牌的烧牌撞车（占位推演的固有边界）：{exc}"
        ) from exc


def hand_view(state, hero_index: int, dealt: dict) -> dict:
    """把引擎状态序列化成前端视图。"""
    positions = positions_for(state.player_count)
    n = state.player_count
    seats = []
    for i in range(n):
        seats.append({
            "pos": positions[i],
            "stack": state.stacks[i],
            "bet": int(state.bets[i]),          # 当前街已投入的下注
            "in_hand": bool(state.statuses[i]),
            "is_hero": i == hero_index,
        })
    actor = positions[state.actor_index] if state.actor_index is not None else None
    street = STREETS[state.street_index] if state.street_index is not None else "over"
    # 23：不可再选的牌 = 已被"真实观察"的牌——英雄底牌、已发公共牌。
    # 烧牌是系统内部动作，玩家从没见过它，不能禁选；占位牌是虚构推演，
    # 同样不能锁死真实选牌（模拟摊牌时亮出的占位牌也不算观察）。
    filler_cards = set()
    for i, cards_i in dealt.items():
        if i != hero_index:
            filler_cards.update(cards_i)
    known = set(dealt[hero_index]) | {
        repr(c) for street in state.board_cards for c in street}
    simulated = bool(filler_cards)
    taken = sorted(known)
    view = {
        "simulated": simulated,
        "positions": list(positions),
        "street": street,
        "board": [repr(c) for street in state.board_cards for c in street],
        "taken": taken,
        "pot": state.total_pot_amount,
        "seats": seats,
        "actor": actor,
        "hand_over": not state.status,
        "hero": {
            "pos": positions[hero_index],
            "cards": dealt[hero_index],
            "stack": state.stacks[hero_index],
        },
    }
    if actor is not None:
        view["to_call"] = state.checking_or_calling_amount
        view["min_raise_to"] = state.min_completion_betting_or_raising_to_amount
        view["max_raise_to"] = state.max_completion_betting_or_raising_to_amount
    if not state.status:
        view["payoffs"] = {positions[i]: int(p) for i, p in enumerate(state.payoffs) if p != 0}
        view["starting_stacks"] = [int(x) for x in state.starting_stacks]
        # 摊牌亮牌：亮所有没弃牌的玩家。不能用 state.statuses 判断——
        # 结算后 HAND_KILLING 会清掉输家的在局状态；弃牌者从操作日志取。
        folded = {op.player_index for op in state.operations
                  if type(op).__name__ == "Folding"}
        view["revealed"] = {
            positions[i]: list(dealt[i]) for i in range(n) if i not in folded
        }
    return view


def replay(config: dict, hero_pos: str, hero_cards, ops) -> dict:
    """无状态回放：配置 + 英雄手牌 + 操作序列 → 视图。"""
    state, hero_index, dealt = replay_state(config, hero_pos, hero_cards, ops)
    return hand_view(state, hero_index, dealt)


def replay_state(config: dict, hero_pos: str, hero_cards, ops):
    """同 replay，但返回 (引擎状态, 英雄座位索引, 发牌表) 供建议计算复用。

    整次回放在固定随机序列下进行（确定性）：同一输入的占位牌、烧牌、
    模拟结果完全一致——view 与 advice 并发回放不会漂移。声明的公共牌
    若恰好撞上该序列的模拟烧牌（真实烧牌不可见，玩家选到的公共牌是
    合法观察），自动换下一个序列重试；极限情况才报错。
    """
    positions = positions_for(int(config.get("player_count", 6)))
    if hero_pos not in positions:
        raise TableError(f"未知座位：{hero_pos!r}，可选 {list(positions)}")
    hero_index = positions.index(hero_pos)
    declared = []
    for op in (ops or []):
        if isinstance(op, dict) and op.get("op") == "board":
            cards = op.get("cards") or []
            if isinstance(cards, list):
                declared.extend(str(c).strip() for c in cards)

    last_err: Optional[TableError] = None
    for attempt in range(8):
        rng_state = random.getstate()
        random.seed(_FILLER_SEED + attempt)
        try:
            state = build_state(config)
            dealt = deal_hole(state, hero_index, hero_cards, reserve=declared)
            apply_ops(state, ops, dealt, hero_index)
            return state, hero_index, dealt
        except TableError as exc:
            if "撞车" in str(exc):
                last_err = exc      # 模拟烧牌占用真实公共牌：换洗牌序列重放
                continue
            raise
        finally:
            random.setstate(rng_state)
    raise last_err


# ------------------------------------------------------------------ v2 连续牌桌

def replay_context(context: dict, hero_cards, ops):
    """v2 回放：物理座位上下文 → 引擎。

    context 须先经 backend.table_context.validate_context 验证。
    返回 (state, plan, dealt)；dealt 以引擎索引为键。
    """
    from . import table_context   # 延迟导入避免循环

    plan = table_context.engine_plan(context)
    state = _create_engine_state(
        plan["player_count"], plan["raw_blinds"][0], plan["raw_blinds"][1],
        plan["antes"], plan["stacks"], plan["min_bet"])
    hero_index = plan["seat_to_index"][plan["hero_seat_id"]]
    declared_board = [c for op in ops if op.get("op") == "board"
                      for c in op.get("cards", [])]
    dealt = deal_hole(state, hero_index, hero_cards, reserve=declared_board)
    v2_ops = []
    for op in ops:
        if op.get("op") == "action":
            sid = op.get("seat_id")
            idx = plan["seat_to_index"].get(sid)
            if idx is None:
                raise TableError(f"seat_id {sid!r} 不是本手入局者")
            v2_ops.append({"op": "action", "seat_index": idx,
                           "type": op.get("type"), "to": op.get("to")})
        else:
            v2_ops.append(op)
    apply_ops(state, v2_ops, dealt=dealt, hero_index=hero_index)
    return state, plan, dealt


def build_view_v2(state, context: dict, plan: dict, dealt: dict) -> dict:
    """A02：v2 视图——以固定 seat_id 为身份，含结算预览。"""
    occ_by_seat = {p["seat_id"]: p["occupant_id"] for p in context["participants"]}
    chips_by_seat = {p["seat_id"]: p["starting_chips"] for p in context["participants"]}
    hero_seat = plan["hero_seat_id"]
    hero_index = plan["seat_to_index"][hero_seat]
    roles = plan["roles_by_seat"]
    folded = _folded_indices(state)
    seats = []
    for i, sid in enumerate(plan["index_to_seat"]):
        seats.append({
            "seat_id": sid,
            "occupant_id": occ_by_seat[sid],
            "range_position": plan["range_positions"][i],
            "roles": roles.get(sid, []),
            "stack": int(state.stacks[i]),
            "bet": int(state.bets[i]),
            "in_hand": bool(state.statuses[i]),
            "folded": i in folded,
            "all_in": bool(state.statuses[i]) and state.stacks[i] == 0,
            "is_hero": sid == hero_seat,
            "starting_chips": chips_by_seat[sid],
        })
    actor_seat = (plan["index_to_seat"][state.actor_index]
                  if state.actor_index is not None else None)
    street = STREETS[state.street_index] if state.street_index is not None else "over"
    known = set(dealt[hero_index]) | {
        repr(c) for street_cards in state.board_cards for c in street_cards}
    view = {
        "street": street,
        "board": [repr(c) for street_cards in state.board_cards for c in street_cards],
        "taken": sorted(known),
        "pot": state.total_pot_amount,
        "seats": seats,
        "actor_seat_id": actor_seat,
        "hand_over": not state.status,
        "hero": {
            "seat_id": hero_seat,
            "occupant_id": context["hero_occupant_id"],
            "cards": dealt[hero_index],
            "stack": int(state.stacks[hero_index]),
        },
        "settlement_preview": settlement_preview(state, plan, dealt),
    }
    if actor_seat is not None:
        view["to_call"] = state.checking_or_calling_amount
        view["min_raise_to"] = state.min_completion_betting_or_raising_to_amount
        view["max_raise_to"] = state.max_completion_betting_or_raising_to_amount
    return view


def _folded_indices(state) -> set:
    """从操作日志取弃牌者引擎索引（状态位在结算后会被清除）。"""
    return {op.player_index for op in state.operations
            if type(op).__name__ == "Folding"}


def settlement_preview(state, plan: dict, dealt: dict) -> dict:
    """C03：结算预览——分类依据动作与已知信息，不用模拟赢家推断。

    - 手进行中（未手工结束）：not_ready，不给任何行建议。
    - 手结束且无摊牌（其余全弃，含未跟注超额退回）：全部行 verified。
    - 手结束但有摊牌：摊牌者行 unknown（占位牌收益不是事实），弃牌者 verified。
    """
    folded = _folded_indices(state)
    if state.status:
        rows = []
        for i, sid in enumerate(plan["index_to_seat"]):
            if i in folded:
                rows.append({"seat_id": sid, "suggested_chips": int(state.stacks[i]),
                             "source": "verified", "reason": "folded"})
            else:
                rows.append({"seat_id": sid, "suggested_chips": None,
                             "source": "unknown", "reason": "incomplete_record"})
        return {"status": "not_ready", "reason": "incomplete_record", "rows": rows}
    showdown = (plan["player_count"] - len(folded)) >= 2
    rows = []
    for i, sid in enumerate(plan["index_to_seat"]):
        if showdown and i not in folded:
            rows.append({"seat_id": sid, "suggested_chips": None,
                         "source": "unknown", "reason": "unobserved_showdown"})
        else:
            rows.append({"seat_id": sid, "suggested_chips": int(state.stacks[i]),
                         "source": "verified", "reason": "folded" if i in folded else "fold_win"})
    return {
        "status": "needs_manual",
        "reason": "unknown_showdown" if showdown else "fold_win",
        "rows": rows,
    }
