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
        """评估一次对局，返回 (胜, 平, 负) 的 0/1 计数。"""
        scores = [_score(hero_cards + full_board)]
        for hand in fixed_hands:
            scores.append(_score([_card(c) for c in hand] + full_board))
        for hand in extra_hands:
            scores.append(_score([_card(c) for c in hand] + full_board))
        best = max(scores)
        if scores[0] < best:
            return (0, 0, 1)
        return (1, 0, 0) if scores.count(best) == 1 else (0, 1, 0)

    exact = False
    wins = ties = losses = 0
    trials = iterations

    if not range_pools and need_board == 0:
        wins, ties, losses = score_trial(board_cards)
        exact, trials = True, 1
    elif not range_pools and need_board == 1:
        for extra in base_deck:
            w, t, l = score_trial(board_cards + [_card(extra)])
            wins += w
            ties += t
            losses += l
        exact, trials = True, len(base_deck)
    else:
        for _ in range(iterations):
            avail_set = set(base_deck)
            villain_hands = []
            for pool in range_pools:
                while True:
                    pairs = [p for p, _ in pool]
                    weights = [w for _, w in pool]
                    c1, c2 = rng.choices(pairs, weights=weights)[0]
                    # range 组合可能撞上已发出的牌，撞了就重抽（与真实发牌等价：
                    # 冲突的组合本来就不可能同时出现）
                    if c1 in avail_set and c2 in avail_set:
                        avail_set.discard(c1)
                        avail_set.discard(c2)
                        villain_hands.append([c1, c2])
                        break

            avail = list(avail_set)
            full_board = board_cards + [_card(c) for c in rng.sample(avail, need_board)]
            w, t, l = score_trial(full_board, villain_hands)
            wins += w
            ties += t
            losses += l

    total = wins + ties + losses
    result = {
        "engine": ENGINE,
        "exact": exact,
        "iterations": trials,
        "elapsedMs": int((time.perf_counter() - start) * 1000),
        "win": round(wins * 100 / total, 2),
        "tie": round(ties * 100 / total, 2),
        "lose": round(losses * 100 / total, 2),
    }
    return result
