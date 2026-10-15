"""ICM（独立筹码模型）：把决赛桌记分牌换算成奖金期望。

Malmuth–Harville 模型：名次从冠军端依次确定，下一个名次归谁的概率
= 他的筹码占剩余筹码的比例（冠军概率 = 筹码占比，其余名次递归）。
用子集 DP 精确计算（9 人桌只有 512 个子集，微秒级、逐位可复现）：

    g[S] = "第 1..|S| 名已确定且恰好是集合 S" 的概率
    从 S 转移时，第 |S|+1 名归 i ∈ ¬S 的概率 = chips_i / chips(¬S)

数学上 Σ_i equity_i = 奖池总额（tests 校验），且权益对筹码单调
（记分牌多 → 期望奖金不更低，tests 校验）。
"""

from __future__ import annotations

from itertools import combinations
import math

__all__ = ["icm_equities", "ICMError"]


class ICMError(ValueError):
    """ICM 输入不合法。"""


def validate_payouts(payouts, player_count=None, *, allow_empty=False, ordered=True, allow_zero=False):
    """所有HTTP入口共用金额规则；先比较上限，避免超大整数转float溢出。"""
    if not isinstance(payouts, list) or (not payouts and not allow_empty):
        raise ICMError("奖金结构不能为空")
    if player_count is not None and len(payouts) > player_count:
        raise ICMError("奖金名次数不能超过人数")
    if any(isinstance(p, bool) or not isinstance(p, (int, float))
           or not 0 <= p <= 2**53 - 1 or not math.isfinite(p) for p in payouts):
        raise ICMError("奖金须为非负有限数值且不超过安全金额上限")
    if payouts and (not 0 <= sum(payouts) <= 2**53 - 1 or (not allow_zero and sum(payouts) == 0)
                    or (ordered and payouts != sorted(payouts, reverse=True))):
        raise ICMError("奖金须递减且总额在安全金额范围内")
    return payouts


def icm_equities(stacks, payouts) -> list[float]:
    """按 Malmuth–Harville 模型返回每个玩家的奖金期望。

    stacks: 记分牌（正数）；payouts: 名次奖金，从第 1 名开始，可短于人数（其余为 0）。
    返回与 stacks 同序的期望奖金列表，和 = 奖池总额。
    """
    validate_payouts(payouts, len(stacks), ordered=False, allow_zero=True)
    stacks = [float(s) for s in stacks]
    payouts = [float(p) for p in payouts]
    n = len(stacks)
    if n < 2:
        raise ICMError("ICM 至少需要 2 名玩家")
    if any(s <= 0 for s in stacks):
        raise ICMError("记分牌必须全为正数")
    if not payouts or any(p < 0 for p in payouts):
        raise ICMError("奖金结构不能为空且不能有负数")

    # g[mask] = 名次已确定的玩家恰为 mask 的概率；从冠军开始逐个"落位"
    eq = [0.0] * n
    g: dict[int, float] = {0: 1.0}
    for size in range(n):                    # 已落位 size 人 → 现在定第 size+1 名
        for S in combinations(range(n), size):
            mask = 0
            for i in S:
                mask |= 1 << i
            p = g.get(mask)
            if not p:
                continue
            rem_mask = ((1 << n) - 1) ^ mask
            tot = sum(stacks[i] for i in range(n) if rem_mask >> i & 1)
            prize = payouts[size] if size < len(payouts) else 0.0
            for i in range(n):
                if rem_mask >> i & 1:
                    eq[i] += p * stacks[i] / tot * prize
                    nxt = mask | (1 << i)
                    g[nxt] = g.get(nxt, 0.0) + p * stacks[i] / tot
    return eq
