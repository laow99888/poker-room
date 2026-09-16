"""决策建议：按翻前线路自动给对手配范围，算胜率、SPR 与带概率的行动建议。

建议是"权益 vs 底池赔率"的启发式（非 GTO 求解），输出混合策略：
每个可选动作带一个建议百分比，供玩家参考节奏，而非机械执行。
"""

from . import charts, equity, opponents as opponents_mod, textures
from .ranges import expand_range
from .table import STREETS, TableError, positions_for


def _classify_lines(state) -> dict:
    """从引擎操作日志推断每个玩家的翻前线路。

    rfi=首次加注；three_bet=再加注；call=跟过注；pending=还没轮到。
    """
    raise_count = {i: 0 for i in range(state.player_count)}
    has_called = {i: False for i in range(state.player_count)}
    first_raise_order = {i: None for i in range(state.player_count)}
    global_raises = 0

    for op in state.operations:
        name = type(op).__name__
        if name == "CompletionBettingOrRaisingTo":
            pi = op.player_index
            if first_raise_order[pi] is None:
                first_raise_order[pi] = global_raises
            raise_count[pi] += 1
            global_raises += 1
        elif name == "CheckingOrCalling":
            if op.amount > 0:
                has_called[op.player_index] = True

    lines = {}
    for i in range(state.player_count):
        if raise_count[i] >= 1:
            lines[i] = "rfi" if first_raise_order[i] == 0 else "three_bet"
        elif has_called[i]:
            lines[i] = "call"
        else:
            lines[i] = "pending"
    return lines


def _range_for(position: str, line: str):
    """按座位 + 线路取默认范围，返回 (手牌代码列表, 具体组合列表, 中文标签)。"""
    if line == "rfi":
        codes = charts.situation_codes(position, "rfi")
        label = charts.SITUATION_LABEL["rfi"]
    elif line == "three_bet":
        codes = charts.situation_codes(position, "three_bet")
        label = charts.SITUATION_LABEL["three_bet"]
    elif line == "call":
        codes = charts.situation_codes(position, "call")
        if not codes and position in ("SB", "BB"):
            codes = charts.situation_codes(position, f"defend_{position}")
        label = charts.SITUATION_LABEL["call"]
    else:  # pending：盲注尚未行动、面对下注
        if position in ("SB", "BB"):
            codes = charts.situation_codes(position, f"defend_{position}")
            label = charts.SITUATION_LABEL[f"defend_{position}"]
        else:
            combos = charts.facing_raise_range(position)
            return [], combos, charts.SITUATION_LABEL["facing_raise"]
    combos = charts.expand_range(",".join(codes)) if codes else []
    return codes, combos, label


_CHEN_VALUES = {"A": 10, "K": 8, "Q": 7, "J": 6}
_RANK_ORDER_FULL = "AKQJT98765432"


def _chen_score(combo) -> float:
    """Chen 公式：翻前起手牌强度的经典近似评分（用于范围内部排序）。"""
    ra, rb = combo[0][0], combo[1][0]
    suited = combo[0][1] == combo[1][1]

    def val(r):
        if r == "A":
            return 10
        if r == "K":
            return 8
        if r == "Q":
            return 7
        if r == "J":
            return 6
        return (_RANK_ORDER_FULL.index(r) + 2) / 2

    hi, lo = max(val(ra), val(rb)), min(val(ra), val(rb))
    if ra == rb:
        return max(5.0, hi * 2)
    score = hi + lo / 2
    if suited:
        score += 2
    gap = abs(_RANK_ORDER_FULL.index(ra) - _RANK_ORDER_FULL.index(rb)) - 1
    score -= {0: 0, 1: 1, 2: 2, 3: 4}.get(gap, 5)
    return round(score, 1)


def _narrow_by_strength(combos, keep_frac):
    """按 Chen 强度保留范围内最强的 keep_frac 比例组合。"""
    if keep_frac >= 1 or len(combos) < 4:
        return combos, 1.0
    ranked = sorted(combos, key=lambda c: _chen_score(c), reverse=True)
    keep = max(1, int(len(ranked) * keep_frac))
    return ranked[:keep], keep / len(ranked)


def _mix_facing_bet(eff, required, spr, opponent_count):
    """面对下注：按权益余量给混合策略。返回 (mix, reason)。"""
    margin = eff - required
    if opponent_count >= 3:
        margin -= 8  # 多人池需要更紧
    if spr is not None and spr <= 1.5 and margin >= 0:
        return [("allin", 1.0)], "SPR 极低且权益领先：直接全下，不留后手"
    if margin >= 30:
        return [("raise", 0.75, 0.8), ("call", 0.25)], "权益大幅领先：以价值加注为主"
    if margin >= 15:
        return [("raise", 0.55, 0.7), ("call", 0.45)], "权益领先：价值加注与跟注保护混合"
    if margin >= 5:
        return [("call", 0.75), ("raise", 0.25, 0.5)], "权益略高于所需：以跟注为主、偶尔加注施压"
    if margin >= -3:
        return [("call", 0.55), ("fold", 0.45)], "权益接近底池赔率：边际局面，凭读牌取舍"
    return [("fold", 0.85), ("call", 0.15)], "权益低于所需胜率：大多数时候应弃牌"


def _mix_no_bet(eff, spr, opponent_count):
    """面前无注：以过牌为主，按权益决定价值下注/小注诈唬。"""
    threshold = 60 + 6 * (opponent_count - 1)  # 多人池收紧价值阈值
    if eff >= threshold + 10:
        return [("bet", 0.7, 0.75), ("check", 0.3)], "权益明显领先：大部分时候价值下注"
    if eff >= threshold:
        return [("bet", 0.55, 0.55), ("check", 0.45)], "权益领先：半价值下注与过牌混合"
    if eff >= threshold - 10:
        return [("check", 0.7), ("bet", 0.3, 0.33)], "权益一般：以过牌为主、偶尔小注施压"
    return [("check", 0.85), ("bet", 0.15, 0.33)], "权益落后：过牌为主、偶尔小注诈唬"


def _build_recommendation(state, hero_index, eq, to_call, pot, required, opponent_count,
                          range_adv_val=None):
    """把权益与底池赔率翻译成带概率的行动建议。"""
    eff = eq["win"] + eq["tie"] / 2
    eff_stack = state.get_effective_stack(hero_index)
    spr = round(eff_stack / pot, 1) if pot > 0 else None
    bb = state.blinds_or_straddles[1]
    min_to = state.min_completion_betting_or_raising_to_amount
    max_to = state.max_completion_betting_or_raising_to_amount

    if to_call > 0:
        mix, reason = _mix_facing_bet(eff, required, spr, opponent_count)
    else:
        mix, reason = _mix_no_bet(eff, spr, opponent_count)
        # 第一层算法：范围优势调制无注时的下注倾向
        if range_adv_val is not None:
            if range_adv_val >= 8:
                mix = [(m[0], min(1.0, m[1] + 0.10) if m[0] == "bet" else m[1], *m[2:]) for m in mix]
                total = sum(m[1] for m in mix)
                mix = [(m[0], m[1] / total, *m[2:]) for m in mix]
                reason = reason + "；范围优势在你，可以更主动地施压"
            elif range_adv_val <= -8:
                mix = [(m[0], m[1] + 0.10 if m[0] == "check" else m[1], *m[2:]) for m in mix]
                total = sum(m[1] for m in mix)
                mix = [(m[0], m[1] / total, *m[2:]) for m in mix]
                reason = reason + "；范围优势在对手，以过牌为主"

    # 面对全下且不可再加注时，剔除加注/全下选项并归一化
    max_to = state.max_completion_betting_or_raising_to_amount
    if max_to is None and to_call > 0:
        mix = [m for m in mix if m[0] in ("call", "fold")]
        if not mix:
            mix = [("call", 1.0)]
        total0 = sum(m[1] for m in mix)
        mix = [(m[0], m[1] / total0, *m[2:]) for m in mix]

    # 筹码结构调制：谁覆盖谁直接改变施压与冒险的价值
    hero_remaining = state.stacks[hero_index]
    opp_remaining = [state.stacks[i] for i in range(state.player_count)
                     if i != hero_index and state.statuses[i]]
    covers_all = bool(opp_remaining) and hero_remaining >= max(opp_remaining)
    covered_deep = any(opp > hero_remaining for opp in opp_remaining)
    stack_note = ""
    aggr_delta = 0.0
    if covers_all and opponent_count > 0:
        stack_note = "你的筹码覆盖所有对手，加注施压的风险相对低"
        if to_call == 0 and eff >= 45:
            aggr_delta = 0.15   # 筹码领先：更倾向施压
    elif covered_deep:
        stack_note = "有深筹码在你身后，边缘牌避免打大池"
        margin_now = eff - required
        if to_call > 0 and margin_now < 15:
            aggr_delta = -0.10  # 被深筹码覆盖：收紧加注倾向
    if aggr_delta:
        adjusted = []
        for item in mix:
            if item[0] in ("raise", "bet"):
                adjusted.append((item[0], max(0.05, min(1.0, item[1] + aggr_delta)), *item[2:]))
            else:
                adjusted.append(item)
        total = sum(it[1] for it in adjusted)
        mix = [(it[0], it[1] / total, *it[2:]) for it in adjusted]
        reason = reason + "；" + stack_note
    # 调制/过滤后重排：保证主建议永远是概率最高的动作
    mix = sorted(mix, key=lambda m: m[1], reverse=True)

    def to_bb(chips):
        return round(chips / bb, 1) if bb > 0 else None

    def clamp_to(chips):
        # 对手不足额全下时"最小加注"可能高于剩余上限，此时只能全下
        if max_to < min_to:
            return int(max_to)
        return int(max(min_to, min(max_to, chips)))

    rendered = []
    for item in mix:
        action, pct = item[0], item[1]
        frac = item[2] if len(item) > 2 else None
        entry = {"action": action, "pct": int(round(pct * 100))}
        if action == "raise":
            to = clamp_to(to_call + int((pot + to_call) * frac))
            entry["to_chips"] = int(to)
            entry["to_bb"] = to_bb(to)
            entry["label"] = f"加注到 {to_bb(to)} BB"
        elif action == "bet":
            to = clamp_to(int(pot * frac))
            entry["to_chips"] = int(to)
            entry["to_bb"] = to_bb(to)
            entry["label"] = f"下注 {to_bb(to)} BB"
        elif action == "call":
            entry["label"] = f"跟注 {to_bb(to_call)} BB" if to_call > 0 else "过牌"
        elif action == "allin":
            entry["to_chips"] = int(max_to)
            entry["to_bb"] = to_bb(max_to)
            entry["label"] = f"全下 {to_bb(max_to)} BB"
        elif action == "check":
            entry["label"] = "过牌"
        else:
            entry["label"] = "弃牌"
        rendered.append(entry)

    advice_note = ""
    if spr is not None and spr <= 3 and any(m["action"] in ("raise", "bet") for m in rendered):
        advice_note = "SPR 偏低，价值牌可考虑直接全下压制"
    if spr is not None and spr >= 8:
        advice_note = "SPR 很深，避免用边缘牌卷入大池"

    return {
        "primary": rendered[0]["label"],
        "mix": rendered,
        "reason": reason,
        "spr": spr,
        "spr_note": advice_note,
        "stack_note": stack_note,
        "covers_all": covers_all,
        "effective_stack": int(eff_stack),
    }


def advice_for(state, hero_index: int, iterations: int, seed=None, names=None) -> dict:
    """计算英雄当前决策建议（必须轮到英雄行动）。"""
    if state.status is False:
        raise TableError("这手牌已经结束")
    if state.actor_index != hero_index:
        positions = positions_for(state.player_count)
        raise TableError(f"还没轮到你行动（当前轮到 {positions[state.actor_index]}）")

    positions = positions_for(state.player_count)
    lines = _classify_lines(state)
    board = [repr(c) for street in state.board_cards for c in street]
    hero_cards = [repr(c) for c in tuple(state.get_down_cards(hero_index))]

    opponents = []
    villains = []
    names = names or {}
    for i in range(state.player_count):
        if i == hero_index or not state.statuses[i]:
            continue
        pos = positions[i]
        codes, combos, label = _range_for(pos, lines[i])
        name = (names.get(pos) or "").strip()
        stats = opponents_mod.get_stats(name) if name else None
        narrowed = False
        keep_frac = 1.0
        if stats and stats["hands"] >= 5 and combos and lines[i] in ("rfi", "three_bet"):
            implied = len(combos) * 100 / 1326
            observed = stats.get("pfr_pct") or 0
            if 0 < observed < implied:
                combos, keep_frac = _narrow_by_strength(combos, observed / implied)
                narrowed = True
        opponents.append({
            "pos": pos,
            "name": name or None,
            "stats": stats,
            "narrowed": narrowed,
            "line": lines[i],
            "situation": label,
            "combos": len(combos),
            "stack": state.stacks[i],                       # 剩余筹码
            "risk_vs_hero": min(state.stacks[i], state.stacks[hero_index]),  # 对你的风险敞口
        })
        if combos:
            villains.append({"type": "combos",
                             "combos": [[a, b] for a, b in combos]})

    eq = equity.simulate(hero_cards, villains, board, iterations, seed=seed)

    to_call = state.checking_or_calling_amount
    pot = state.total_pot_amount
    required = round(to_call * 100 / (pot + to_call), 2) if to_call > 0 else 0.0
    eff = eq["win"] + eq["tie"] / 2
    ev_call = round(eff / 100 * pot - (1 - eff / 100) * to_call, 2)

    bb = state.blinds_or_straddles[1]
    ante = state.antes[0]
    hero_stack = state.stacks[hero_index]
    orbit_cost = state.blinds_or_straddles[0] + bb + ante * state.player_count

    # 第一层算法：牌面结构 + 范围优势/坚果优势
    texture = textures.analyze_board(board) if board else None
    range_adv = None
    nut_adv = None
    hero_codes, hero_combos, _ = _range_for(positions[hero_index], lines[hero_index])
    villain_merged = []
    for opp in opponents:
        codes, combos, _ = _range_for(opp["pos"], opp["line"])
        villain_merged.extend(combos)
    if board and hero_combos and villain_merged:
        range_adv = textures.range_advantage(hero_combos, villain_merged, board,
                                             iterations=min(3000, max(1500, iterations // 10)),
                                             seed=seed)
        nut_adv = textures.nut_advantage(hero_combos, villain_merged, board,
                                         runouts=12, seed=seed)

    recommendation = _build_recommendation(
        state, hero_index, eq, to_call, pot, required, len(opponents),
        range_adv_val=(range_adv["adv"] if range_adv else None))

    return {
        "equity": eq,
        "opponents": opponents,
        "to_call": to_call,
        "pot": pot,
        "required_eq": required,
        "ev_call": ev_call,
        "min_raise_to": state.min_completion_betting_or_raising_to_amount,
        "max_raise_to": state.max_completion_betting_or_raising_to_amount,
        "effective_stack": int(state.get_effective_stack(hero_index)),
        "m_value": round(hero_stack / orbit_cost, 1) if orbit_cost > 0 else None,
        "note": "对手范围按其翻前线路自动估算，可在范围图中调整",
        "recommendation": recommendation,
        "texture": texture,
        "range_adv": range_adv,
        "nut_adv": nut_adv,
    }
