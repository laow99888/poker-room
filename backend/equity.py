"""蒙特卡洛胜率引擎。

评估器适配层：当前环境（Python 3.14）装不了 eval7 的 C 扩展，
回退到纯 Python 的 treys。两者的差异被 _card/_score 封装，
上层逻辑不感知具体引擎。
"""

import random
import time

from .ranges import InvalidHandError, expand_range

try:
    import eval7

    ENGINE = "eval7"

    def _card(text):
        return eval7.Card(text)

    def _score(cards):
        """7 张牌的牌力分数，越大越强。"""
        return eval7.evaluate(cards)

except ImportError:
    from treys import Card as _TCard, Evaluator as _TEvaluator

    ENGINE = "treys"
    _evaluator = _TEvaluator()

    def _card(text):
        return _TCard.new(text)

    def _score(cards):
        # treys 的 evaluate 返回 1..7462，越小越强，这里取负统一为越大越强
        return -_evaluator.evaluate(cards[2:], cards[:2])


ALL_CARDS = [r + s for r in "AKQJT98765432" for s in "cdhs"]


class EquityError(ValueError):
    """输入不合法（重复牌、牌数不对等）。"""


def _parse_card(text: str, seen: set) -> str:
    t = str(text).strip()
    if len(t) == 3 and t[1].upper() == "1":  # 容忍 "10s" 写法
        t = "T" + t[2]
    if len(t) != 2 or t[0].upper() not in "AKQJT98765432" or t[1].lower() not in "cdhs":
        raise EquityError(f"无效的牌：{text!r}（正确示例：As、Kh、Td）")
    card = t[0].upper() + t[1].lower()
    if card in seen:
        raise EquityError(f"存在重复的牌：{card}")
    seen.add(card)
    return card


def validate_inputs(hero, villains, board):
    """校验并归一化输入，返回 (hero, board, villain 规格列表)。"""
    seen: set = set()
    if len(hero) != 2:
        raise EquityError("你的底牌必须是 2 张牌")
    hero = [_parse_card(c, seen) for c in hero]

    if len(board) not in (0, 3, 4, 5):
        raise EquityError(f"公共牌只能是 0、3、4 或 5 张，当前 {len(board)} 张")
    board = [_parse_card(c, seen) for c in board]

    if not 1 <= len(villains) <= 8:
        raise EquityError(f"对手数量需在 1 到 8 人之间，当前 {len(villains)} 人")

    specs = []
    for idx, v in enumerate(villains, start=1):
        kind = v.get("type")
        if kind == "hand":
            if len(v.get("cards", [])) != 2:
                raise EquityError(f"对手 {idx} 的手牌必须是 2 张牌")
            cards = tuple(_parse_card(c, seen) for c in v["cards"])
            specs.append(("hand", cards))
        elif kind == "range":
            text = (v.get("text") or "").strip()
            if not text:
                raise EquityError(f"对手 {idx} 的范围不能为空")
            try:
                combos = expand_range(text)
            except InvalidHandError as exc:
                raise EquityError(f"对手 {idx} 范围有误：{exc}") from exc
            specs.append(("range", combos))
        elif kind == "combos":
            pairs = v.get("combos") or []
            weights = v.get("weights")
            if weights is not None and len(weights) != len(pairs):
                raise EquityError(f"对手 {idx} 的权重数与组合数不一致")
            cleaned = []
            for pair in pairs:
                if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                    raise EquityError(f"对手 {idx} 的组合必须是恰好两张牌：{pair!r}")
                local: set = set()
                a = _parse_card(pair[0], local)
                b = _parse_card(pair[1], local)
                cleaned.append((a, b))
            if not cleaned:
                raise EquityError(f"对手 {idx} 的组合范围为空")
            if weights is None:
                weights = [1] * len(cleaned)
            # 池元素统一为 ((c1, c2), weight)
            specs.append(("range", list(zip(cleaned, weights))))  # 具体组合与范围共用同一采样池逻辑
        else:
            raise EquityError(f"对手 {idx} 的类型未知：{kind!r}")
    return hero, board, specs


def simulate(hero, villains, board, iterations, seed=None):
    """计算 hero 的胜/平/负百分比。

    所有手牌确定且公共牌已到转牌/河牌时改为精确枚举（无抽样误差），
    其余情况用蒙特卡洛模拟。
    """
    hero, board, specs = validate_inputs(hero, villains, board)
    rng = random.Random(seed)
    start = time.perf_counter()

    hero_cards = [_card(c) for c in hero]
    board_cards = [_card(c) for c in board]
    need_board = 5 - len(board_cards)

    # 采样全程用字符串，只在评估时转成引擎的牌对象，避免两套表示混用
    # 池元素统一为 ((c1, c2), weight)，无权重时权重为 1
    range_pools = []
    for kind, payload in specs:
        if kind != "range":
            continue
        if payload and isinstance(payload[0][0], tuple):
            pools = [(pair, w) for pair, w in payload]
        else:
            pools = [(pair, 1) for pair in payload]
        range_pools.append(pools)
    fixed_hands = [list(payload) for kind, payload in specs if kind == "hand"]
    used = set(hero) | set(board)
    for hand in fixed_hands:
        used.update(hand)
    base_deck = [c for c in ALL_CARDS if c not in used]

    def score_trial(full_board, extra_hands=()):
        """评估一次对局，返回 (胜, 平, 负) 计数与英雄的底池份额。

        份额：英雄赢 → 1/平分人数；输 → 0。多人平分底池时
        win+tie/2 会高估权益（三家平分得 50%，真实份额 1/3），
        因此所有 EV 计算必须用份额。
        """
        scores = [_score(hero_cards + full_board)]
        for hand in fixed_hands:
            scores.append(_score([_card(c) for c in hand] + full_board))
        for hand in extra_hands:
            scores.append(_score([_card(c) for c in hand] + full_board))
        best = max(scores)
        if scores[0] < best:
            return (0, 0, 1), 0.0
        if scores.count(best) == 1:
            return (1, 0, 0), 1.0
        return (0, 1, 0), 1.0 / scores.count(best)

    # --- 01：范围池预过滤死牌（英雄/公共牌/明手），消除"永远撞车"的死循环来源 ---
    known = used
    filtered_pools = []
    for pool in range_pools:
        keep = [(pair, w) for pair, w in pool if pair[0] not in known and pair[1] not in known]
        if not keep:
            raise EquityError(
                "对手范围的全部组合都与已知牌（底牌/公共牌）冲突，无法计算")
        filtered_pools.append(keep)

    exact = False
    wins = ties = losses = 0.0
    share_sum = 0.0
    trials = iterations

    if not filtered_pools and need_board == 0:
        (wins, ties, losses), sh = score_trial(board_cards)
        share_sum = sh
        exact, trials = True, 1
    elif not filtered_pools and need_board == 1:
        for extra in sorted(base_deck):   # 18：固定牌序，set 不直接进抽样
            (w, t, l), sh = score_trial(board_cards + [_card(extra)])
            wins += w; ties += t; losses += l
            share_sum += sh
        exact, trials = True, len(base_deck)
    else:
        # --- 01：联合拒绝采样。任一对手撞牌就整组重抽（不是只重抽后一家），
        # 保证合法联合分布与对手顺序无关；带尝试上限，不可能时明确报错。
        failed = 0
        for _ in range(iterations):
            for _attempt in range(50):
                avail_set = set(base_deck)
                villain_hands = []
                ok_trial = True
                for pool in filtered_pools:
                    pairs = [p for p, _ in pool]
                    weights = [w for _, w in pool]
                    c1, c2 = rng.choices(pairs, weights=weights)[0]
                    if c1 in avail_set and c2 in avail_set:
                        avail_set.discard(c1)
                        avail_set.discard(c2)
                        villain_hands.append([c1, c2])
                    else:
                        ok_trial = False
                        break
                if ok_trial:
                    break
            else:
                failed += 1
                continue   # 该次试验 50 次都没抽出合法联合 → 跳过并计数
            avail = sorted(avail_set)   # 18：不把 set 的遍历序交给抽样
            full_board = board_cards + [_card(c) for c in rng.sample(avail, need_board)]
            (w, t, l), sh = score_trial(full_board, villain_hands)
            wins += w; ties += t; losses += l
            share_sum += sh
        if failed > iterations // 2:
            raise EquityError(
                "对手范围与已知牌联合冲突过多，无法采样（检查范围是否与公共牌/底牌矛盾）")

    total = wins + ties + losses
    eff_trials = total if total else 1
    result = {
        "engine": ENGINE,
        "exact": exact,
        "iterations": trials,
        "elapsedMs": int((time.perf_counter() - start) * 1000),
        "win": round(wins * 100 / eff_trials, 2),
        "tie": round(ties * 100 / eff_trials, 2),
        "lose": round(losses * 100 / eff_trials, 2),
        # 06：真实底池份额（多人平分正确），EV/所需胜率一律用它
        "equity": round(share_sum * 100 / eff_trials, 2),
    }
    return result
