"""第一层算法：牌面结构分析 + 范围优势（RA）/ 坚果优势（NA）。

理论依据（德扑进阶分析的标准概念）：
- 范围优势 RA：双方"整体范围"在这块牌面上的平均权益差。你范围更强，
  就可以更高频率地下注施压；
- 坚果优势 NA：双方范围里能在这块牌面做出"两对以上"顶级牌型的组合占比。
  坚果优势方才敢打大池、才有价值全下的底气。

计算全部用固定种子的蒙特卡洛近似：完全离线、可复现、毫秒级。
"""

import random
from collections import Counter

from treys import Card as TCard, Evaluator

from .ranges import expand_range

_evaluator = Evaluator()
_ALL = [r + s for s in "cdhs" for r in "AKQJT98765432"]
_RANKS = "AKQJT98765432"


def _t(card: str):
    return TCard.new(card)


def analyze_board(board) -> dict:
    """牌面结构：对子、同花、连接度与干湿标签。board 为 3-5 张牌字符串列表。"""
    if not board:
        return {"paired": False, "trips": False, "suit_max": 0, "monotone": False,
                "two_tone": False, "connected": 0, "wetness": 0, "label": "空"}
    ranks = [c[0] for c in board]
    suits = [c[1] for c in board]
    rank_counts = Counter(ranks)
    suit_counts = Counter(suits)

    paired = any(v >= 2 for v in rank_counts.values())
    trips = any(v >= 3 for v in rank_counts.values())
    suit_max = max(suit_counts.values())
    n_suits = len(suit_counts)
    monotone = n_suits == 1 and len(board) >= 3
    two_tone = n_suits == 2 and len(board) >= 3

    idxs = sorted(_RANKS.index(r) for r in ranks)
    connected = sum(1 for a, b in zip(idxs, idxs[1:]) if b - a == 1)

    wet = suit_max * 2 + connected * 2 + (1 if paired else 0) + (5 if trips else 0)
    label = "湿润" if wet >= 8 else ("中等" if wet >= 4 else "干旱")

    return {
        "paired": paired, "trips": trips,
        "suit_max": suit_max, "monotone": monotone, "two_tone": two_tone,
        "connected": connected, "wetness": wet, "label": label,
    }


def range_equity(hero_combos, villain_combos, board, iterations=3000, seed=None):
    """双方范围在当前牌面上的平均权益（hero 侧百分比，含一半平分）。"""
    rng = random.Random(seed)
    board = list(board)
    need = 5 - len(board)
    board_t = [_t(c) for c in board]
    hc = list(hero_combos)
    vc = list(villain_combos)
    if not hc or not vc:
        return None

    score = 0.0
    count = 0
    attempts = iterations * 3
    board_set = set(board)
    while count < iterations and attempts > 0:
        attempts -= 1
        h = hc[rng.randrange(len(hc))]
        v = vc[rng.randrange(len(vc))]
        # 与公共牌冲突的组合不可能成立，跳过
        if h[0] in board_set or h[1] in board_set or v[0] in board_set or v[1] in board_set:
            continue
        avail = [c for c in _ALL if c not in (h[0], h[1], v[0], v[1]) and c not in board]
        extra = rng.sample(avail, need)
        full_t = board_t + [_t(c) for c in extra]
        hero_t = [_t(h[0]), _t(h[1])]
        villain_t = [_t(v[0]), _t(v[1])]
        hs = _evaluator.evaluate(full_t, hero_t)
        vs = _evaluator.evaluate(full_t, villain_t)
        count += 1
        if hs < vs:
            score += 1
        elif hs == vs:
            score += 0.5
    if count == 0:
        return None
    return round(score / count * 100, 1)


def range_advantage(hero_combos, villain_combos, board, iterations=3000, seed=None):
    """范围优势：双方范围在当前牌面的平均权益差（正数=hero 范围占优）。"""
    hero_eq = range_equity(hero_combos, villain_combos, board, iterations, seed)
    if hero_eq is None:
        return None
    return {"hero_eq": hero_eq, "villain_eq": round(100 - hero_eq, 1),
            "adv": round(2 * hero_eq - 100, 1)}


def nut_advantage(hero_combos, villain_combos, board, runouts=12, seed=None):
    """坚果优势近似：双方范围做出"两对以上"牌型的组合占比对比（百分比）。"""
    rng = random.Random(seed)
    board = list(board)
    need = 5 - len(board)
    if need <= 0 or not hero_combos or not villain_combos:
        return None
    dead_board = set(board)

    def strong_ratio(combos):
        total = strong = 0
        for _ in range(runouts):
            extra = rng.sample([c for c in _ALL if c not in dead_board], need)
            full = board + extra
            dead = set(full)
            full_t = [_t(c) for c in full]
            for a, b in combos:
                if a in dead or b in dead:
                    continue
                rank = _evaluator.evaluate(full_t, [_t(a), _t(b)])
                total += 1
                if _evaluator.get_rank_class(rank) <= 7:   # 两对及以上
                    strong += 1
        return strong / total if total else None   # 范围整体不可评估（如全部与公共牌冲突）

    hero_r = strong_ratio(hero_combos)
    villain_r = strong_ratio(villain_combos)
    if hero_r is None or villain_r is None:
        return None
    return {"hero": round(hero_r * 100, 1), "villain": round(villain_r * 100, 1),
            "adv": round(hero_r * 100 - villain_r * 100, 1)}


def combos_from_range_text(text: str):
    """范围字符串 → 具体组合列表（供外部快速调用）。"""
    return expand_range(text)
