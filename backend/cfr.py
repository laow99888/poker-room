"""第三层：单街 CFR（反事实遗憾最小化）求解器。

用 CFR+（遗憾匹配 + 负遗憾截断 + 交替更新 + 线性加权平均策略）在两人零和
扩展式博弈上迭代求解，输出接近纳什均衡的混合策略。

模块分三部分：
  1. 通用 CFR 引擎（solve_tree / solve_pairs）——与具体扑克规则无关；
  2. 教科书锚点局 kuhn_tree / akq_tree——有精确理论解，用于自校验；
  3. 河牌单街求解器 solve_river——真实德扑组合 + treys 牌力 + 分桶抽象。

抽象方式：双方范围先按"对范围平均权益"分成若干桶，CFR 在桶对上迭代；
每个组合独立成桶时退化为精确解（锚点局正是如此校验）。
纪律：任何接入建议链路的改动，必须先让锚点局收敛到理论解（tests + 推演脚本）。
"""

from __future__ import annotations

try:
    from treys import Card as _TCard, Evaluator as _Evaluator
    _EVAL = _Evaluator()
except ImportError:  # pragma: no cover - 环境缺依赖时直接暴露
    _TCard = None
    _EVAL = None

__all__ = [
    "solve_tree", "solve_pairs", "kuhn_tree", "akq_tree",
    "solve_river", "CFRError",
]


class CFRError(ValueError):
    """求解器输入不合法或无法求解。"""


# ---------------------------------------------------------------------------
# 通用 CFR+ 引擎
# ---------------------------------------------------------------------------

def _regret_matching(regrets: list[float]) -> list[float]:
    pos = [r if r > 0.0 else 0.0 for r in regrets]
    s = sum(pos)
    if s > 0:
        return [p / s for p in pos]
    return [1.0 / len(regrets)] * len(regrets)


def _avg_strategy(tables: dict) -> dict:
    avg = {}
    for iset, (_, strat_sum, labels) in tables.items():
        s = sum(strat_sum)
        if s > 0:
            avg[iset] = {labels[k]: strat_sum[k] / s for k in range(len(labels))}
        else:
            u = 1.0 / len(labels)
            avg[iset] = {lab: u for lab in labels}
    return avg


def _showdown_net0(v: int, c0: float, c1: float) -> float:
    """摊牌净收益（座位 0 视角，多出部分自动退回）。v ∈ {+1, 0, -1}。"""
    ce = c0 if c0 < c1 else c1
    if v > 0:
        return c1
    if v < 0:
        return -ce
    return (c1 - c0) / 2.0


def _cfr(node, u: int, p0: float, p1: float, i: int, j: int, vmat, tables: dict, t: int) -> float:
    """一次遍历。返回子树在传入 reach 下的期望值（座位 0 视角）。"""
    kind = node["kind"]
    if kind == "terminal":
        return node["value"]
    if kind == "fold":
        return node["net0"]
    if kind == "showdown":
        return _showdown_net0(vmat[i][j], node["c0"], node["c1"])
    if kind == "chance":
        s = 0.0
        for pr, ch in node["children"]:
            s += pr * _cfr(ch, u, p0, p1, i, j, vmat, tables, t)
        return s

    seat = node["seat"]
    pk = node.get("pk")
    if pk is None:  # 河牌骨架：信息集按桶（遍历参数）区分；玩具局私牌烤在节点里
        pk = i if seat == 0 else j
    # 节点级缓存：同一骨架节点在同一桶下的信息集条目只查一次表
    cache = node.get("_e")
    if cache is None:
        cache = node["_e"] = {}
    entry = cache.get(pk)
    if entry is None:
        iset = (seat, pk, node["hist"])
        entry = tables.get(iset)
        if entry is None:
            entry = tables[iset] = ([0.0] * len(node["labels"]),
                                    [0.0] * len(node["labels"]),
                                    node["labels"])
        cache[pk] = entry
    regs, strat_sum, _ = entry
    sigma = _regret_matching(regs)

    vals = []
    children = node["children"]
    for k in range(len(sigma)):
        ch = children[k]
        sk = sigma[k]
        if seat == 0:
            vals.append(_cfr(ch, u, p0 * sk, p1, i, j, vmat, tables, t))
        else:
            vals.append(_cfr(ch, u, p0, p1 * sk, i, j, vmat, tables, t))
    ev = sum(sigma[k] * vals[k] for k in range(len(sigma)))

    if seat == u:
        opp = p1 if seat == 0 else p0
        p_own = p0 if seat == 0 else p1
        sgn = 1.0 if seat == 0 else -1.0   # 节点值统一座位 0 视角，座位 1 更新需翻转
        for k in range(len(sigma)):
            regs[k] += (vals[k] - ev) * sgn * opp
            if regs[k] < 0.0:          # CFR+：负遗憾截断
                regs[k] = 0.0
            strat_sum[k] += t * p_own * sigma[k]   # 线性加权平均
    return ev


def solve_tree(tree, iterations: int = 4000) -> dict:
    """求解显式博弈树（锚点局）。返回 {(seat, 私牌, 历史): {动作: 频率}}。

    固定迭代数、无时间截断：同参数结果完全可复现。
    """
    tables: dict = {}
    for t in range(1, iterations + 1):
        _cfr(tree, (t - 1) % 2, 1.0, 1.0, 0, 0, None, tables, t)
    return _avg_strategy(tables)


def solve_pairs(skeleton, pairs, vmat, iterations: int = 1200) -> dict:
    """在骨架树上按 (桶) 对迭代。pairs = [(概率, 座位0桶, 座位1桶), ...]。

    固定迭代数：range_cap 已把单次迭代成本封顶，总耗时确定，结果逐位可复现。
    """
    tables: dict = {}
    for t in range(1, iterations + 1):
        u = (t - 1) % 2
        for pp, a, b in pairs:
            # 机会概率同时计入双方 reach：对手 reach 承担机会权重（反事实值需要），
            # 己方 reach 仅用于平均策略加权；逐对概率归一，不影响均衡方向。
            _cfr(skeleton, u, pp, pp, a, b, vmat, tables, t)
    return _avg_strategy(tables)


# ---------------------------------------------------------------------------
# 教科书锚点局（自校验用；理论解断言见 tests/test_cfr_toys.py）
# ---------------------------------------------------------------------------

def kuhn_tree():
    """Kuhn 扑克：J<Q<K 各发一张，每人先注 1，限一次下注。

    已知纳什均衡（先注单位制）：先手（座位 0）理论游戏值 −1/18，
    且座位 0 持 J 必须以恰好 1/3 概率诈唬下注（由座位 1 持 Q 的底池赔率强制）。
    """
    def branch(c0, c1):
        return {"kind": "action", "seat": 0, "hist": "", "pk": c0,
                "labels": ["check", "bet"],
                "children": [
                    {"kind": "action", "seat": 1, "hist": "c", "pk": c1,
                     "labels": ["check", "bet"],
                     "children": [
                         {"kind": "terminal", "value": 1.0 if c0 > c1 else -1.0},
                         {"kind": "action", "seat": 0, "hist": "cb", "pk": c0,
                          "labels": ["fold", "call"],
                          "children": [
                              {"kind": "terminal", "value": -1.0},
                              {"kind": "terminal", "value": 2.0 if c0 > c1 else -2.0},
                          ]},
                     ]},
                    {"kind": "action", "seat": 1, "hist": "b", "pk": c1,
                     "labels": ["fold", "call"],
                     "children": [
                         {"kind": "terminal", "value": 1.0},
                         {"kind": "terminal", "value": 2.0 if c0 > c1 else -2.0},
                     ]},
                ]}

    children = []
    for c0 in range(3):
        for c1 in range(3):
            if c0 == c1:
                continue
            children.append((1.0 / 6.0, branch(c0, c1)))
    return {"kind": "chance", "children": children}


def akq_tree(pot: float = 1.0, bet: float = 1.0):
    """AKQ 单街单挑：座位 0 范围 {A, Q}（极化），座位 1 恒为 K（抓诈牌），座位 0 先行动。

    经典解：座位 0 持 Q 诈唬频率 = B/(P+B)；座位 1 跟注频率 = P/(P+B)。
    （P = 进入本街底池，B = 下注额；底池注时两者均为 1/2。）
    """
    half = pot / 2.0
    call_c = half + bet

    def branch(c0):
        return {"kind": "action", "seat": 0, "hist": "", "pk": c0,
                "labels": ["check", "bet"],
                "children": [
                    {"kind": "terminal", "value": half if c0 == 0 else -half},
                    {"kind": "action", "seat": 1, "hist": "b", "pk": 1,
                     "labels": ["fold", "call"],
                     "children": [
                         {"kind": "terminal", "value": half},
                         {"kind": "terminal", "value": call_c if c0 == 0 else -call_c},
                     ]},
                ]}

    return {"kind": "chance", "children": [(0.5, branch(0)), (0.5, branch(1))]}


# ---------------------------------------------------------------------------
# 河牌单街求解器（真实德扑组合）
# ---------------------------------------------------------------------------

_CARD_CACHE: dict = {}


def _t(card: str) -> int:
    v = _CARD_CACHE.get(card)
    if v is None:
        v = _CARD_CACHE[card] = _TCard.new(card)
    return v


def _showdown_sign(hero_combo, villain_combo, board_t) -> int:
    hr = _EVAL.evaluate(board_t, [ _t(c) for c in hero_combo ])
    vr = _EVAL.evaluate(board_t, [ _t(c) for c in villain_combo ])
    return 1 if hr < vr else (-1 if hr > vr else 0)  # treys 排名越小越强


def _stratified_subset(combos, opponent, board_t, limit: int):
    """范围过大时按对抽样对手的探测权益分层等距取样（确定性）。"""
    n = len(combos)
    if n <= limit:
        return combos, [0.0] * n, False
    step = max(1, len(opponent) // 20)
    probe = opponent[::step][:20]
    strength = []
    for c in combos:
        vals = [_showdown_sign(c, p, board_t) for p in probe]
        strength.append(sum(vals) / len(vals))
    order = sorted(range(n), key=lambda i: (-strength[i], combos[i]))
    k = min(limit, n)
    picks = sorted({order[round(i * (n - 1) / (k - 1))] for i in range(k)})
    return [combos[i] for i in picks], strength, True


def solve_river(board, hero_combos, villain_combos, pot_bb: float, to_call_bb: float = 0.0,
                stack_bb: float = 10.0, bet_sizes=(1.0,), raise_size: float = 1.0,
                buckets: int = 8, iterations: int = 1200,
                range_cap: int = 120, keep=()) -> dict:
    """河牌单街求解。英雄恒为座位 0（当前决策者），面额 BB。

    范围超过 range_cap 时按探测权益分层抽样；keep 中的组合必保留。
    固定迭代数（范围已封顶，单次迭代成本有界）→ 同参数结果完全可复现。
    返回 dict：组合与桶号、桶对胜负矩阵、平均策略、骨架树等。
    组合过少/冲突/依赖缺失时抛 CFRError。
    """
    if _TCard is None:
        raise CFRError("缺少 treys 依赖")
    if len(board) != 5:
        raise CFRError("河牌求解需要恰好 5 张公共牌")
    hero_all = sorted({tuple(c) for c in hero_combos if not (set(c) & set(board))})
    villain_all = sorted({tuple(c) for c in villain_combos if not (set(c) & set(board))})
    if not hero_all or not villain_all:
        raise CFRError("过滤死牌后范围为空，无法求解")
    if pot_bb <= 0 or to_call_bb < 0 or stack_bb <= 0:
        raise CFRError("底池、跟注额与有效筹码必须为正")

    board_t = [_t(c) for c in board]
    keep_set = {frozenset(c) for c in keep}

    hero, hero_probe_strength, hero_capped = _stratified_subset(
        hero_all, villain_all, board_t, range_cap)
    for c in hero_all:                      # keep 的组合丢失时补回（换掉末位）
        if frozenset(c) in keep_set and c not in hero:
            hero[-1] = c
            hero.sort()
            break
    villain, _, villain_capped = _stratified_subset(
        villain_all, hero_all, board_t, range_cap)

    nh, nv = len(hero), len(villain)
    vmat = [[None] * nv for _ in range(nh)]
    for i, hc in enumerate(hero):
        h0, h1 = _t(hc[0]), _t(hc[1])
        hc_set = set(hc)
        row = vmat[i]
        for j, vc in enumerate(villain):
            if hc_set & set(vc):
                continue
            hr = _EVAL.evaluate(board_t, [h0, h1])
            vr = _EVAL.evaluate(board_t, [_t(vc[0]), _t(vc[1])])
            row[j] = 1 if hr < vr else (-1 if hr > vr else 0)

    hero_bucket, nb_h, hero_strength = _bucket_assign(hero, vmat, buckets)
    vmat_t = [[vmat[i][j] for i in range(nh)] for j in range(nv)]
    villain_bucket, nb_v, villain_strength = _bucket_assign(villain, vmat_t, buckets)

    # 桶对聚合：桶对 (A,B) 的摊牌值 = 桶内合法组合对的平均胜负，权重 = 合法对数
    nb = max(nb_h, nb_v)
    bvmat = [[0.0] * nb_v for _ in range(nb_h)]
    cnt = [[0] * nb_v for _ in range(nb_h)]
    for i in range(nh):
        bi = hero_bucket[i]
        for j in range(nv):
            v = vmat[i][j]
            if v is None:
                continue
            bj = villain_bucket[j]
            bvmat[bi][bj] += v
            cnt[bi][bj] += 1
    pairs = []
    for x in range(nb_h):
        for y in range(nb_v):
            if cnt[x][y]:
                bvmat[x][y] /= cnt[x][y]
                pairs.append((cnt[x][y], x, y))
    total = sum(w for w, _, _ in pairs)
    pairs = [(w / total, x, y) for w, x, y in pairs]
    if not pairs:
        raise CFRError("英雄与对手范围完全冲突，无法求解")

    base = max(0.0, (pot_bb - to_call_bb) / 2.0)
    c0 = base
    c1 = base + max(0.0, to_call_bb)
    stack_bb = max(stack_bb, c0, c1)
    skeleton = _skeleton(to_call_bb > 1e-9, c0, c1, stack_bb,
                         tuple(bet_sizes), raise_size, max_raises=1)

    # 固定迭代数：结果确定，耗时由 range_cap 与 iterations 共同封顶
    avg = solve_pairs(skeleton, pairs, bvmat, iterations=iterations)
    return {
        "hero_combos": hero,
        "villain_combos": villain,
        "hero_bucket": hero_bucket,
        "villain_bucket": villain_bucket,
        "bucket_count": nb,
        "hero_strength": hero_strength,
        "villain_strength": villain_strength,
        "pairs": len(pairs),
        "avg_strategy": avg,
        "iterations": iterations,
        "hero_capped": hero_capped,
        "villain_capped": villain_capped,
        "root_labels": skeleton["labels"],
        "skeleton": skeleton,
    }


def _bucket_assign(combos, vmat_rows, n_buckets):
    """按对范围平均权益分桶（0 = 最强）。返回 (每组合桶号, 实际桶数, 平均权益)。"""
    n = len(combos)
    n_buckets = max(1, min(n_buckets, n))
    strength = []
    for i in range(n):
        vals = [v for v in vmat_rows[i] if v is not None]
        strength.append(sum(vals) / len(vals) if vals else 0.0)
    order = sorted(range(n), key=lambda i: (-strength[i], combos[i]))
    bucket = [0] * n
    for rank, i in enumerate(order):
        bucket[i] = min(rank * n_buckets // n, n_buckets - 1)
    return bucket, n_buckets, strength


def _skeleton(facing: bool, c0: float, c1: float, stack: float,
              bet_sizes, raise_size: float, max_raises: int = 1):
    """河牌行动骨架（与具体组合无关，逐桶对共享）。面额统一为 BB。

    行动标签：check / bet / call / fold / raise（多个下注尺度时为 bet:P/2 等）。
    """
    def build(actor, a, b, raises, hist, facing_):
        committed = a if actor == 0 else b
        opp_committed = b if actor == 0 else a
        outstanding = opp_committed - committed
        labels, children = [], []

        def act_node():
            return {"kind": "action", "seat": actor, "hist": hist,
                    "labels": labels, "children": children}

        if facing_:  # 面对下注/加注
            labels.append("fold")
            # 弃牌净收益：座位 0 视角 = 对方已投入（自己那份沉没）
            children.append({"kind": "fold",
                             "net0": b if actor == 1 else -a})
            labels.append("call")
            children.append({"kind": "showdown", "c0": opp_committed, "c1": opp_committed})
            if raises < max_raises:
                extra = stack - committed
                target = opp_committed + raise_size * (a + b)
                target = min(target, committed + extra)
                if target > opp_committed:
                    na, nb = (target, opp_committed) if actor == 0 else (opp_committed, target)
                    labels.append("raise")
                    child = build(1 - actor, na, nb, raises + 1, hist + "r", True)
                    child["raise_to"] = target          # 加注到的总额（本街）
                    children.append(child)
            return act_node()

        labels.append("check")
        if hist.endswith("k"):  # 双方连续过牌 → 摊牌
            children.append({"kind": "showdown", "c0": a, "c1": b})
        else:
            children.append(build(1 - actor, a, b, raises, hist + "k", False))
        extra = stack - committed
        if extra > 0 and raises < max_raises:
            for f in bet_sizes:
                target = min(committed + f * (a + b), committed + extra)
                if target <= committed:
                    continue  # 筹码不足以按该尺度下注
                na, nb = (target, opp_committed) if actor == 0 else (opp_committed, target)
                label = "bet" if len(bet_sizes) == 1 else f"bet:P×{f:g}"
                labels.append(label)
                child = build(1 - actor, na, nb, raises + 1, hist + "b", True)
                child["bet_add"] = target - committed   # 本街新增投入
                child["allin"] = target >= committed + extra - 1e-9
                children.append(child)
        return act_node()

    return build(0, c0, c1, 0, "", facing)

