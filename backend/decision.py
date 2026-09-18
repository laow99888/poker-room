"""决策建议：按翻前线路自动给对手配范围，算胜率、SPR 与带概率的行动建议。

建议是"权益 vs 底池赔率"的启发式（非 GTO 求解），输出混合策略：
每个可选动作带一个建议百分比，供玩家参考节奏，而非机械执行。
"""

from . import charts, cfr as cfr_mod, equity, opponents as opponents_mod, textures
from .ranges import expand_range
from .table import STREETS, TableError, positions_for

_RANK_ORDER = "AKQJT98765432"


def _norm_combo(cards) -> tuple:
    """把两张底牌规范化成 (高牌, 低牌) 组合，如 ['kd','As'] -> ('As','Kd')。"""
    return tuple(sorted((c[0].upper() + c[1].lower() for c in cards),
                        key=lambda c: _RANK_ORDER.index(c[0])))


def _action_class(action: str) -> str:
    if action in ("bet", "raise", "allin"):
        return "aggr"
    if action in ("call", "check"):
        return "passive"
    return "fold"


def _cfr_street_block(state, hero_index, hero_cards, hero_combos, villain_combos,
                      board, recommendation) -> dict:
    """第三层：翻牌/转牌/河牌 CFR 均衡参考（单挑限定；降级时不影响主建议）。

    河牌为精确单街解；翻牌/转牌把剩余牌 rollout 成期望胜率作终值（近似，
    solve_street 返回 approximate=True）。
    """
    try:
        live = [i for i in range(state.player_count)
                if i != hero_index and state.statuses[i]]
        street = {3: "翻牌", 4: "转牌", 5: "河牌"}.get(len(board))
        if street is None:
            return {"supported": False, "reason": "CFR 参考从翻牌开始（翻牌/转牌/河牌单挑底池）"}
        if len(live) != 1:
            return {"supported": False, "reason": "CFR 参考仅支持单挑底池"}
        if not hero_combos or not villain_combos:
            return {"supported": False, "reason": "范围信息不足，无法求解"}
        # 03：金额口径三路分离——累计投入（含前街）/死钱/身后剩余筹码。
        # stack_bb 不能再拿"身后剩余"去减"已在池的钱"（老代码重复扣减，
        # 全下额度失真）。PokerKit 的 stack 是身后筹码，start-stack = 累计投入。
        bb = state.blinds_or_straddles[1]
        if bb <= 0:
            return {"supported": False, "reason": "盲注配置异常"}
        opp = live[0]
        pot_bb = state.total_pot_amount / bb
        to_call_bb = state.checking_or_calling_amount / bb
        s0 = (state.starting_stacks[hero_index] - state.stacks[hero_index]) / bb
        s1 = (state.starting_stacks[opp] - state.stacks[opp]) / bb
        money = (s0, s1, max(0.0, pot_bb - s0 - s1),
                 state.stacks[hero_index] / bb, state.stacks[opp] / bb)
        hero_range = list(hero_combos)
        actual = _norm_combo(hero_cards)
        actual_added = not any(frozenset(c) == frozenset(actual) for c in hero_range)
        if actual_added:
            hero_range.append(actual)

        # 21：引擎层面没有加注权（如对手已全下，min/max_raise_to 为 None）时
        # 明确禁用 raise 分支——不允许骨架输出物理上非法的加注策略
        can_raise = state.max_completion_betting_or_raising_to_amount is not None

        res = cfr_mod.solve_street(board, hero_range, villain_combos,
                                   money=money, bet_sizes=(0.5, 1.0),
                                   buckets=8, iterations=1600, keep=[actual],
                                   allow_raise=can_raise)

        idx = next(i for i, c in enumerate(res["hero_combos"])
                   if frozenset(c) == frozenset(actual))
        bucket = res["hero_bucket"][idx]
        strat = res["avg_strategy"].get((0, bucket, ""))
        if not strat:
            return {"supported": False, "reason": "未找到对应决策点"}

        sk = res["skeleton"]
        # 33：骨架金额是"全手累计投入"口径；界面与引擎（min/max_raise_to）
        # 都按"本街加注到"计。展示前必须转换并夹在引擎合法上下限内，
        # 不能把累计数直接当本街金额输出。
        hero_bets_bb = state.bets[hero_index] / bb          # 本街已投入
        prev_street_bb = s0 - hero_bets_bb                  # 前街累计（含前注死钱）
        lo_to = state.min_completion_betting_or_raising_to_amount
        hi_to = state.max_completion_betting_or_raising_to_amount

        def to_street_amount(cumulative_bb: float) -> float:
            amt = cumulative_bb - prev_street_bb
            if lo_to is not None and hi_to is not None:
                amt = min(max(amt, lo_to / bb), hi_to / bb)
            return amt

        mix = []
        for label, child in zip(sk["labels"], sk["children"]):
            p = strat.get(label, 0.0)
            if p < 0.005:
                continue
            entry = {"action": label.split(":")[0], "freq": round(p, 3),
                     "pct": int(round(p * 100))}
            if label == "check":
                entry["label"] = "过牌"
            elif label == "call":
                entry["label"] = f"跟注 {round(to_call_bb, 1)} BB"
            elif label == "fold":
                entry["label"] = "弃牌"
            elif label.startswith("bet"):
                add = child.get("bet_add", 0.0)
                entry["label"] = ("全下" if child.get("allin")
                                  else f"下注 {round(add, 1)} BB")
            elif label == "raise":
                amt = to_street_amount(child.get("raise_to", 0.0))
                allin = hi_to is not None and amt >= hi_to / bb - 1e-9
                entry["label"] = ("全下" if allin
                                  else f"加注到 {round(amt, 1)} BB")
            mix.append(entry)
        # 多个下注尺度因筹码不足聚成同一种"全下"时合并展示
        merged = []
        for m in mix:
            for q in merged:
                if q["label"] == m["label"]:
                    q["freq"] = round(q["freq"] + m["freq"], 3)
                    q["pct"] = int(round(q["freq"] * 100))
                    break
            else:
                merged.append(m)
        mix = sorted((m for m in merged if m["freq"] >= 0.005),
                     key=lambda m: -m["freq"])

        heur_top = recommendation["mix"][0]["action"] if recommendation["mix"] else None
        cfr_top = mix[0]["action"] if mix else None
        s_raw = res["hero_strength"][idx]
        equity_pct = round(s_raw * 100, 1)   # 河牌/翻牌/转牌统一 eq ∈ [0,1]
        return {
            "supported": True,
            "street": street,
            "mix": mix,
            "bucket": bucket,
            "buckets": res["bucket_count"],
            "pairs": res["pairs"],
            "iterations": res["iterations"],
            "equity_vs_range": equity_pct,
            "hero_range_combos": len(res["hero_combos"]),
            "villain_range_combos": len(res["villain_combos"]),
            "range_capped": bool(res.get("hero_capped") or res.get("villain_capped")),
            "approximate": bool(res.get("approximate")),
            "actual_added": actual_added,
            "agree": (heur_top is not None and cfr_top is not None
                      and _action_class(heur_top) == _action_class(cfr_top)),
            "note": ("CFR+ 均衡参考：双方范围按权益分桶抽象后求解"
                     + ("，剩余牌 rollout 成期望终值（近似）" if res.get("approximate") else "")),
        }
    except cfr_mod.CFRError as exc:
        return {"supported": False, "reason": str(exc)}
    except Exception as exc:  # 求解层任何意外都不得影响主建议
        return {"supported": False, "reason": f"求解器内部错误：{exc}"}


def player_intel(state) -> dict:
    """翻前行为情报：每座位的线路分类与行为桶（第二层对手建模的输入）。

    line: rfi / three_bet / call / check / fold / pending
    kind: open / threebet / limp / call_raise / check / fold_raise / fold_noraise / pending
    """
    n = state.player_count
    raise_count = {i: 0 for i in range(n)}
    has_vol = {i: False for i in range(n)}
    first_raise_order = {i: None for i in range(n)}
    folded_facing_raise = {i: False for i in range(n)}
    folded_free = {i: False for i in range(n)}
    checked_free = {i: False for i in range(n)}
    called_after_raise = {i: False for i in range(n)}
    limped = {i: False for i in range(n)}
    raised = {i: False for i in range(n)}
    global_raises = 0

    preflop = True
    for op in state.operations:
        nm = type(op).__name__
        if nm == "BoardDealing":     # 公共牌开出后进入翻后：不再计入翻前分类
            preflop = False
            continue
        i = getattr(op, "player_index", None)
        if i is None or not preflop:
            continue
        if nm == "CompletionBettingOrRaisingTo":
            if first_raise_order[i] is None:
                first_raise_order[i] = global_raises
            raise_count[i] += 1
            raised[i] = True
            global_raises += 1
        elif nm == "CheckingOrCalling":
            if op.amount > 0:
                if global_raises > 0:
                    called_after_raise[i] = True
                else:
                    limped[i] = True
                has_vol[i] = True
            else:
                checked_free[i] = True
        elif nm == "Folding":
            if global_raises > 0:
                folded_facing_raise[i] = True
            else:
                folded_free[i] = True

    intel = {}
    for i in range(n):
        if raised[i]:
            line = "rfi" if first_raise_order[i] == 0 else "three_bet"
            kind = "open" if first_raise_order[i] == 0 else "threebet"
        elif has_vol[i]:
            line = "call"
            kind = "limp" if limped[i] else "call_raise"
        elif folded_facing_raise[i]:
            line = kind = "fold_raise"
        elif folded_free[i]:
            line = kind = "fold_free"
        elif checked_free[i]:
            line = kind = "check"
        else:
            line = kind = "pending"
        intel[i] = {"line": line, "kind": kind}
    return intel


def _classify_lines(state) -> dict:
    """兼容旧调用：返回 {座位索引: line}。"""
    return {i: v["line"] for i, v in player_intel(state).items()}


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

    _LOW = {"T": 5, "9": 4.5, "8": 4, "7": 3.5, "6": 3,
            "5": 2.5, "4": 2, "3": 1.5, "2": 1}
    def val(r):
        if r == "A":
            return 10
        if r == "K":
            return 8
        if r == "Q":
            return 7
        if r == "J":
            return 6
        return _LOW[r]

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
                          range_adv_val=None, eff_override=None):
    """把权益与底池赔率翻译成带概率的行动建议。"""
    # 24/35：优先消费分层加权权益（与 EV 同源），平局半分的高估只在
    # 无分层结果时兜底；多家平分时它会失真
    eff = eff_override if eff_override is not None \
        else eq.get("equity", eq["win"] + eq["tie"] / 2)
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


def _side_pots(state, hero_index: int, to_call: int):
    """把"英雄跟注后可争夺的池"按投入层级分解（24/34）。

    返回 (layers, hero_total, to_call_eff)。to_call_eff 是不足额跟注
    封顶到英雄身后筹码后的实际跟注额——短码全下时引擎报出的 to_call
    可能远超英雄能投的钱，赔率必须按实际投入算。每层 amount 含英雄
    与死钱的切片；对手投入超过英雄总投入的部分在英雄够不到的更深层，
    不计入（可争池口径）。弃牌死钱落在最浅层。
    """
    start = state.starting_stacks
    n = state.player_count
    contrib = [max(0, start[i] - state.stacks[i]) for i in range(n)]
    to_call_eff = min(to_call, state.stacks[hero_index])
    hero_total = contrib[hero_index] + to_call_eff
    levels = sorted({min(contrib[i], hero_total) for i in range(n)
                     if i != hero_index and contrib[i] > 0})
    layers = []
    prev = 0
    for lv in levels:
        if lv <= prev:
            continue
        amount = min(hero_total - prev, lv - prev) + sum(
            min(max(contrib[i] - prev, 0), lv - prev)
            for i in range(n) if i != hero_index)
        seats = [i for i in range(n)
                 if i != hero_index and contrib[i] >= lv]
        layers.append({"amount": amount, "seats": seats})
        prev = lv
    return layers, hero_total, to_call_eff


def advice_for(state, hero_index: int, iterations: int, seed=None, names=None,
               uid="local", learning_enabled: bool = False) -> dict:
    """计算英雄当前决策建议（必须轮到英雄行动）。uid 用于隔离对手档案。

    learning_enabled=False（默认）时只读基础位置范围+本手动作推断，
    不读取具名档案与共享人群统计（契约 A04）。
    """
    if state.status is False:
        raise TableError("这手牌已经结束")
    if state.actor_index != hero_index:
        positions = positions_for(state.player_count)
        raise TableError(f"还没轮到你行动（当前轮到 {positions[state.actor_index]}）")

    positions = positions_for(state.player_count)
    intel = player_intel(state)
    board = [repr(c) for street in state.board_cards for c in street]
    hero_cards = [repr(c) for c in tuple(state.get_down_cards(hero_index))]

    opponents = []
    villains = []
    villain_seats = []
    names = names or {}
    for i in range(state.player_count):
        if i == hero_index or not state.statuses[i]:
            continue
        pos = positions[i]
        line = intel[i]["line"]
        codes, combos, label = _range_for(pos, line)
        name = (names.get(pos) or "").strip() if learning_enabled else ""
        stats = (opponents_mod.get_stats(name, uid=uid)
                 if learning_enabled and name else None)
        pool = opponents_mod.get_pool(pos) if learning_enabled else None
        narrowed = False
        keep_frac = 1.0
        range_source = "位置图"
        # 优先级：具名档案（≥5手）→ 人群位置统计（≥10手）→ 静态图
        if stats and stats["hands"] >= 5 and combos and line in ("rfi", "three_bet"):
            implied = len(combos) * 100 / 1326
            observed = stats.get("pfr_pct") or 0
            if 0 < observed < implied:
                combos, keep_frac = _narrow_by_strength(combos, observed / implied)
                narrowed = True
            range_source = f"个人档案 {name}（{stats['hands']} 手）"
        elif pool and line == "rfi":
            implied = len(combos) * 100 / 1326
            scale = max(0.35, min(1.0, pool["open_pct"] / implied))
            if scale < 1.0:
                combos, keep_frac = _narrow_by_strength(combos, scale)
            range_source = f"人群统计（{pos} 实际开牌率 {pool['open_pct']}%，样本 {pool['hands']} 手）"
        opponents.append({
            "pos": pos,
            "name": name or None,
            "stats": stats,
            "narrowed": narrowed,
            "range_source": range_source,
            "line": line,
            "situation": label,
            "combos": len(combos),
            "stack": state.stacks[i],
            "risk_vs_hero": min(state.stacks[i], state.stacks[hero_index]),
        })
        if combos:
            villains.append({"type": "combos",
                             "combos": [[a, b] for a, b in combos]})
            villain_seats.append(i)

    eq = equity.simulate(hero_cards, villains, board, iterations, seed=seed)

    to_call = state.checking_or_calling_amount
    pot = state.total_pot_amount
    # 24/34：赔率、EV 与建议判定建立在同一份分层计算上。
    # required = 实际跟注额 / 跟注后可争夺总额——补盲（100/400=25%）、
    # 普通池（1000/3450=28.99%）与短码全下（1000/3300=30.30%）同时成立，
    # 英雄够不到的深层边池不再压低门槛。
    layers, hero_total, to_call_eff = _side_pots(state, hero_index, to_call)
    total_amounts = sum(l["amount"] for l in layers)
    required = (round(to_call_eff * 100 / total_amounts, 2)
                if to_call_eff > 0 and total_amounts > 0 else 0.0)
    eff = eq.get("equity", eq["win"] + eq["tie"] / 2)
    ev_call = -float(to_call_eff)
    pot_rows = []
    for layer in layers:
        layer_villains = [villains[k] for k, seat in enumerate(villain_seats)
                          if seat in layer["seats"]]
        if not layer_villains:
            layer_eq = 100.0        # 该层只剩死钱可白拿
        elif len(layer_villains) == len(villains):
            layer_eq = eff          # 与总体同一对手集合，不重复模拟
        else:
            sub = equity.simulate(hero_cards, layer_villains, board,
                                  max(3000, iterations // 4), seed=seed)
            layer_eq = sub["equity"]
        ev_call += layer_eq / 100.0 * layer["amount"]
        pot_rows.append({"amount": layer["amount"],
                         "seats": [positions[i] for i in layer["seats"]],
                         "equity": layer_eq})
    ev_call = round(ev_call, 2)
    equity_share = round(eff, 2)
    # 35：建议判定消费分层加权权益（Σ 权益×层金额 / Σ 层金额），
    # 与 ev_call 同源对账——总体权益 0% 不再覆盖必胜边池
    weighted_eff = (round(100.0 * (ev_call + to_call_eff) / total_amounts, 2)
                    if total_amounts > 0 else eff)

    bb = state.blinds_or_straddles[1]
    ante = state.antes[0]
    hero_stack = state.stacks[hero_index]
    orbit_cost = state.blinds_or_straddles[0] + bb + ante * state.player_count

    # 第一层算法：牌面结构 + 范围优势/坚果优势
    texture = textures.analyze_board(board) if board else None
    range_adv = None
    nut_adv = None
    hero_codes, hero_combos, _ = _range_for(positions[hero_index], intel[hero_index]["line"])
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
        range_adv_val=(range_adv["adv"] if range_adv else None),
        eff_override=weighted_eff)

    cfr_block = _cfr_street_block(state, hero_index, hero_cards, hero_combos,
                                 villain_merged, board, recommendation)

    return {
        "equity": eq,
        "equity_share": equity_share,
        "opponents": opponents,
        "to_call": to_call,
        "pot": pot,
        "required_eq": required,
        "ev_call": ev_call,
        "side_pots": pot_rows,
        "min_raise_to": state.min_completion_betting_or_raising_to_amount,
        "max_raise_to": state.max_completion_betting_or_raising_to_amount,
        "effective_stack": int(state.get_effective_stack(hero_index)),
        "m_value": round(hero_stack / orbit_cost, 1) if orbit_cost > 0 else None,
        "note": "对手范围按其翻前线路自动估算，可在范围图中调整",
        "recommendation": recommendation,
        "cfr": cfr_block,
        "texture": texture,
        "range_adv": range_adv,
        "nut_adv": nut_adv,
    }
