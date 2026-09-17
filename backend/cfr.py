"""第三层：单街 CFR（反事实遗憾最小化）求解器。

用 CFR+（遗憾匹配 + 负遗憾截断 + 交替更新 + 线性加权平均策略）在两人零和
扩展式博弈上迭代求解，输出接近纳什均衡的混合策略。

模块分三部分：
  1. 通用 CFR 引擎（solve_tree / solve_pairs）——与具体扑克规则无关；
  2. 教科书锚点局 kuhn_tree / akq_tree——有精确理论解，用于自校验；
  3. 真实德扑单街求解器 solve_street / solve_river——treys 牌力 + 分桶抽象。

抽象方式：双方范围先按"对范围平均权益"分成若干桶，CFR 在桶对上迭代；
每个组合独立成桶时退化为精确解（锚点局正是如此校验）。
河牌（5 张公共牌）用精确胜负符号；翻牌/转牌把"剩余牌发完的期望胜率"
 rollout 成桶对终值（固定种子，逐位可复现）——这是近似，接口文档注明。
纪律：任何接入建议链路的改动，必须先让锚点局收敛到理论解（tests + 推演脚本）。
"""

from __future__ import annotations

import random
from itertools import combinations

try:
    from treys import Card as _TCard, Evaluator as _Evaluator
    _EVAL = _Evaluator()
except ImportError:  # pragma: no cover - 环境缺依赖时直接暴露
    _TCard = None
    _EVAL = None

__all__ = [
    "solve_tree", "solve_pairs", "kuhn_tree", "akq_tree",
    "solve_river", "solve_street", "CFRError",
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


def _showdown_ev(eq: float, c0: float, c1: float) -> float:
    """摊牌净收益的期望形式：eq ∈ [0,1] 为含平局半分的胜率。

    eq=1/0.5/0 时与 _showdown_net0(+1/0/-1) 完全一致（骨架内摊牌节点 c0==c1）。
    """
    ce = c0 if c0 < c1 else c1
    return eq * c1 - (1.0 - eq) * ce


def _cfr(node, u: int, p0: float, p1: float, i: int, j: int, vmat, tables: dict, t: int) -> float:
    """一次遍历。返回子树在传入 reach 下的期望值（座位 0 视角）。"""
    kind = node["kind"]
    if kind == "terminal":
        return node["value"]
    if kind == "fold":
        return node["net0"]
    if kind == "showdown":
        if node.get("eq"):
            return _showdown_ev(vmat[i][j], node["c0"], node["c1"])
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


def solve_river(board, hero_combos, villain_combos, pot_bb: float = None,
                to_call_bb: float = 0.0,
                stack_bb: float = 10.0, bet_sizes=(1.0,), raise_size: float = 1.0,
                buckets: int = 8, iterations: int = 1200,
                range_cap: int = 120, keep=(), money=None) -> dict:
    """河牌单街求解。英雄恒为座位 0（当前决策者），面额 BB。

    范围超过 range_cap 时按探测权益分层抽样；keep 中的组合必保留。
    固定迭代数（范围已封顶，单次迭代成本有界）→ 同参数结果完全可复现。
    money=(s0, s1, dead, r0, r1) 显式给出金额口径（03：累计投入/死钱/
    身后筹码分离）；缺省时按 (pot_bb, to_call_bb, stack_bb) 派生（兼容旧参数）。
    返回 dict：组合与桶号、桶对权益矩阵（eq ∈ [0,1]）、平均策略、骨架树等。
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

    s0, s1, dead, r0, r1, to_call = _resolve_money(pot_bb, to_call_bb, stack_bb, money)

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
            # 02：终值统一为权益域 eq ∈ [0,1]（含平局半分），而不是 ±1 符号——
            # 桶平均后是混合桶的期望胜率，绝不能被当成"必胜/必败"
            row[j] = 1.0 if hr < vr else (0.5 if hr == vr else 0.0)

    hero_bucket, nb_h, hero_strength = _bucket_assign(hero, vmat, buckets)
    vmat_t = [[vmat[i][j] for i in range(nh)] for j in range(nv)]
    villain_bucket, nb_v, villain_strength = _bucket_assign(villain, vmat_t, buckets)

    # 桶对聚合：桶对 (A,B) 的摊牌值 = 桶内合法组合对的平均权益（eq ∈ [0,1]），
    # 权重 = 合法对数
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

    skeleton = _skeleton(to_call > 1e-9, s0, s1, dead, r0, r1,
                         tuple(bet_sizes), raise_size, max_raises=1,
                         eq_terminal=True)

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


def _bucket_assign(combos, vmat_rows, n_buckets, strengths=None):
    """按对范围平均权益分桶（0 = 最强）。返回 (每组合桶号, 实际桶数, 平均权益)。

    strengths 提供时直接采用（多街 rollout 路径已算好 [0,1] 期望胜率）。
    """
    n = len(combos)
    n_buckets = max(1, min(n_buckets, n))
    if strengths is None:
        strengths = []
        for i in range(n):
            vals = [v for v in vmat_rows[i] if v is not None]
            strengths.append(sum(vals) / len(vals) if vals else 0.0)
    order = sorted(range(n), key=lambda i: (-strengths[i], combos[i]))
    bucket = [0] * n
    for rank, i in enumerate(order):
        bucket[i] = min(rank * n_buckets // n, n_buckets - 1)
    return bucket, n_buckets, strengths


def _skeleton(facing, s0, s1, dead, r0, r1, bet_sizes, raise_size: float,
              max_raises: int = 1, eq_terminal: bool = False):
    """单街行动骨架（与具体组合无关，逐桶对共享）。面额统一为 BB。

    金额三路分离（03）：s0/s1 = 双方累计投入（含前街与死钱之外的所有
    在池筹码），dead = 第三方死钱（多人池其他人的投入），r0/r1 = 双方
    身后剩余筹码。底池 = s0 + s1 + dead。终值为座位 0 的手牌净收益：
    赢家收下对手投入+死钱，输家沉没自己那份，弃牌沉没自己的累计投入。

    行动标签：check / bet / call / fold / raise（多个下注尺度时为 bet:P×f 等）。
    eq_terminal=True 时摊牌节点终值为期望胜率 [0,1]（多街 rollout / 河牌
    桶平均），否则 ±1 符号。

    已知边界：跟注按足额持平建模（不足额全下跟注的边池不在单街骨架内）。
    """
    dead = float(dead)

    def build(actor, a, b, ra, rb, raises, hist, facing_):
        pot = a + b + dead
        committed = a if actor == 0 else b
        opp_committed = b if actor == 0 else a
        behind = ra if actor == 0 else rb
        labels, children = [], []

        def act_node():
            return {"kind": "action", "seat": actor, "hist": hist,
                    "labels": labels, "children": children}

        def after_put(add):
            """行动方再投入 add 后的新 (a, b, ra, rb)。"""
            if actor == 0:
                return a + add, b, ra - add, rb
            return a, b + add, ra, rb - add

        if facing_:  # 面对下注/加注
            labels.append("fold")
            # 弃牌净收益：自己的累计投入全部沉没（座位 0 视角）
            children.append({"kind": "fold",
                             "net0": (b + dead) if actor == 1 else -a})
            labels.append("call")
            # 跟注后双方累计持平；赢则收下对手投入+死钱，输则沉没自己那份
            children.append({"kind": "showdown", "c0": opp_committed,
                             "c1": opp_committed + dead,
                             **({"eq": True} if eq_terminal else {})})
            if raises < max_raises:
                target = min(opp_committed + raise_size * pot,
                             committed + behind)
                if target > opp_committed:
                    na, nb, nra, nrb = after_put(target - committed)
                    labels.append("raise")
                    child = build(1 - actor, na, nb, nra, nrb,
                                  raises + 1, hist + "r", True)
                    child["raise_to"] = target          # 加注到的累计投入
                    children.append(child)
            return act_node()

        labels.append("check")
        if hist.endswith("k"):  # 双方连续过牌 → 摊牌
            children.append({"kind": "showdown", "c0": a, "c1": a + dead,
                             **({"eq": True} if eq_terminal else {})})
        else:
            children.append(build(1 - actor, a, b, ra, rb, raises, hist + "k", False))
        if behind > 1e-12 and raises < max_raises:
            for f in bet_sizes:
                target = min(committed + f * pot, committed + behind)
                if target <= committed + 1e-12:
                    continue  # 身后筹码不足以按该尺度下注
                na, nb, nra, nrb = after_put(target - committed)
                label = "bet" if len(bet_sizes) == 1 else f"bet:P×{f:g}"
                labels.append(label)
                child = build(1 - actor, na, nb, nra, nrb,
                              raises + 1, hist + "b", True)
                child["bet_add"] = target - committed   # 本街新增投入
                child["allin"] = target >= committed + behind - 1e-9
                children.append(child)
        return act_node()

    return build(0, s0, s1, r0, r1, 0, "", facing)


def _resolve_money(pot_bb, to_call_bb, stack_bb, money):
    """统一金额口径（03）。显式 money=(s0, s1, dead, r0, r1) 优先；
    否则按旧参数派生：把整池分解为双方累计投入、无死钱、身后 = stack-投入
    （与历史行为逐位一致，老测试继续成立）。返回 (s0, s1, dead, r0, r1, 跟注额)。"""
    if money is not None:
        if len(money) != 5:
            raise CFRError("money 需为 (s0, s1, dead, r0, r1) 五元组（BB 计）")
        s0, s1, dead, r0, r1 = (float(x) for x in money)
        if min(s0, s1, dead, r0, r1) < 0:
            raise CFRError("money 各项（累计投入/死钱/身后筹码）不能为负")
        return s0, s1, dead, r0, r1, s1 - s0
    if pot_bb is None or pot_bb <= 0 or to_call_bb < 0 or stack_bb <= 0:
        raise CFRError("底池、跟注额与有效筹码必须为正")
    t = max(0.0, to_call_bb)
    base = max(0.0, (pot_bb - t) / 2.0)
    stack_bb = max(stack_bb, base, base + t)
    return base, base + t, 0.0, stack_bb - base, stack_bb - base - t, t


# ---------------------------------------------------------------------------
# 翻牌/转牌多街求解（rollout 终值 + 同一套分桶骨架）
# ---------------------------------------------------------------------------

_ROLLOUT_SEED = 20260919   # 固定种子：rollout 抽样逐位可复现


_ALL52 = tuple(r + s for s in "shdc" for r in "AKQJT98765432")


def _rollout_eq(hero_combo, villain_combo, board, samples, rand):
    """发完剩余公共牌后的胜率（平局记 0.5）；组合冲突返回 None。

    死牌约定：只排除公共牌与这一对组合（对每对组合是干净的边际胜率）。
    剩余牌组合数 ≤ samples 时全枚举（精确），否则固定种子随机抽样。
    """
    if set(hero_combo) & set(villain_combo):
        return None
    need = 5 - len(board)
    board_t = [_t(c) for c in board]
    h0, h1 = _t(hero_combo[0]), _t(hero_combo[1])
    v0, v1 = _t(villain_combo[0]), _t(villain_combo[1])

    def win_on(b):
        hr = _EVAL.evaluate(b, [h0, h1])
        vr = _EVAL.evaluate(b, [v0, v1])
        return 1.0 if hr < vr else (0.0 if hr > vr else 0.5)

    if need <= 0:
        return win_on(board_t)
    dead = set(board) | set(hero_combo) | set(villain_combo)
    deck_t = [_t(c) for c in _ALL52 if c not in dead]
    n_comb = 1
    for k in range(need):
        n_comb = n_comb * (len(deck_t) - k) // (k + 1)
    if n_comb <= samples:   # 全枚举
        wins, n = 0.0, 0
        for run in combinations(deck_t, need):
            wins += win_on(board_t + list(run))
            n += 1
        return wins / n
    wins = 0.0
    for _ in range(samples):
        wins += win_on(board_t + rand.sample(deck_t, need))
    return wins / samples


def solve_street(board, hero_combos, villain_combos, pot_bb: float = None,
                 to_call_bb: float = 0.0,
                 stack_bb: float = 10.0, bet_sizes=(1.0,), raise_size: float = 1.0,
                 buckets: int = 8, iterations: int = 1200,
                 range_cap: int = 120, keep=(),
                 probe_runouts: int = 8, pair_runouts: int = 24,
                 pair_samples: int = 10, money=None) -> dict:
    """单街求解总入口：河牌（5 张）直接走 solve_river 精确路径；
    翻牌（3 张）/转牌（4 张）把"剩余牌随机发完的期望胜率" rollout 成桶对终值。

    money=(s0, s1, dead, r0, r1) 显式金额口径（03），缺省按旧参数派生。
    近似说明：rollout 把后续街的博弈权益压缩成一个静态期望值（不含未来街的行动
    价值），胜率本身由固定种子抽样估计——同参数结果逐位可复现，方向与教科书
    定理一致，但不是该街的精确纳什均衡。
    """
    if _TCard is None:
        raise CFRError("缺少 treys 依赖")
    if len(board) == 5:
        return solve_river(board, hero_combos, villain_combos, pot_bb=pot_bb,
                           to_call_bb=to_call_bb, stack_bb=stack_bb,
                           bet_sizes=tuple(bet_sizes), raise_size=raise_size,
                           buckets=buckets, iterations=iterations,
                           range_cap=range_cap, keep=keep, money=money)
    if len(board) not in (3, 4):
        raise CFRError("多街求解只支持翻牌（3 张）或转牌（4 张）公共牌")

    s0, s1, dead, r0, r1, to_call = _resolve_money(pot_bb, to_call_bb, stack_bb, money)

    hero_all = sorted({tuple(c) for c in hero_combos if not (set(c) & set(board))})
    villain_all = sorted({tuple(c) for c in villain_combos if not (set(c) & set(board))})
    if not hero_all or not villain_all:
        raise CFRError("过滤死牌后范围为空，无法求解")

    board_t = [_t(c) for c in board]
    keep_set = {frozenset(c) for c in keep}
    rand = random.Random(_ROLLOUT_SEED)

    def strength(combos, opponent):
        step = max(1, len(opponent) // 20)
        probe = opponent[::step][:20]
        out = []
        for c in combos:
            vals = [_rollout_eq(c, p, board, probe_runouts, rand)
                    for p in probe]
            vals = [v for v in vals if v is not None]
            out.append(sum(vals) / len(vals) if vals else 0.0)
        return out

    hero, _, _ = _stratified_subset_rollout(hero_all, villain_all, board,
                                            rand, probe_runouts, range_cap)
    for c in hero_all:                      # keep 的组合丢失时补回（换掉末位）
        if frozenset(c) in keep_set and c not in hero:
            hero[-1] = c
            hero.sort()
            break
    villain, _, _ = _stratified_subset_rollout(villain_all, hero_all, board,
                                               rand, probe_runouts, range_cap)

    nh, nv = len(hero), len(villain)
    hero_strength = strength(hero, villain)
    villain_strength = strength(villain, hero)
    hero_bucket, nb_h, _ = _bucket_assign(hero, None, buckets, strengths=hero_strength)
    villain_bucket, nb_v, _ = _bucket_assign(villain, None, buckets, strengths=villain_strength)
    nb = max(nb_h, nb_v)

    # 桶对终值：每对桶抽样若干组合对做 rollout 取平均；权重 = 桶内合法组合对数
    hmap = {b: [i for i in range(nh) if hero_bucket[i] == b] for b in range(nb_h)}
    vmap = {b: [j for j in range(nv) if villain_bucket[j] == b] for b in range(nb_v)}
    bvmat = [[0.0] * nb_v for _ in range(nb)]
    cnt = [[0] * nb_v for _ in range(nb)]
    for x in range(nb_h):
        for y in range(nb_v):
            legal = [(i, j) for i in hmap.get(x, []) for j in vmap.get(y, [])
                     if not (set(hero[i]) & set(villain[j]))]
            if not legal:
                continue
            k = min(pair_samples, len(legal))
            picks = {legal[round(t * (len(legal) - 1) / (k - 1))] for t in range(k)} \
                if k > 1 else {legal[0]}
            vals = [_rollout_eq(hero[i], villain[j], board,
                                pair_runouts, rand) for i, j in picks]
            bvmat[x][y] = sum(vals) / len(vals)
            cnt[x][y] = len(legal)
    pairs = []
    for x in range(nb):
        for y in range(nb_v):
            if cnt[x][y]:
                pairs.append((cnt[x][y], x, y))
    total = sum(w for w, _, _ in pairs)
    pairs = [(w / total, x, y) for w, x, y in pairs]
    if not pairs:
        raise CFRError("英雄与对手范围完全冲突，无法求解")

    skeleton = _skeleton(to_call > 1e-9, s0, s1, dead, r0, r1,
                         tuple(bet_sizes), raise_size, max_raises=1,
                         eq_terminal=True)

    avg = solve_pairs(skeleton, pairs, bvmat, iterations=iterations)
    return {
        "hero_combos": hero,
        "villain_combos": villain,
        "hero_bucket": hero_bucket,
        "villain_bucket": villain_bucket,
        "bucket_count": nb,
        "hero_strength": hero_strength,      # [0,1] 期望胜率（rollout 近似）
        "villain_strength": villain_strength,
        "pairs": len(pairs),
        "avg_strategy": avg,
        "iterations": iterations,
        "hero_capped": len(hero) < len(hero_all),
        "villain_capped": len(villain) < len(villain_all),
        "root_labels": skeleton["labels"],
        "skeleton": skeleton,
        "approximate": True,
    }


def _stratified_subset_rollout(combos, opponent, board, rand,
                               runouts: int, limit: int):
    """多街版分层抽样：按 rollout 探测权益等距取样（确定性）。"""
    n = len(combos)
    if n <= limit:
        return combos, [0.0] * n, False
    step = max(1, len(opponent) // 20)
    probe = opponent[::step][:20]
    strength = []
    for c in combos:
        vals = [_rollout_eq(c, p, board, runouts, rand) for p in probe]
        vals = [v for v in vals if v is not None]
        strength.append(sum(vals) / len(vals) if vals else 0.0)
    order = sorted(range(n), key=lambda i: (-strength[i], combos[i]))
    k = min(limit, n)
    picks = sorted({order[round(i * (n - 1) / (k - 1))] for i in range(k)})
    return [combos[i] for i in picks], strength, True

