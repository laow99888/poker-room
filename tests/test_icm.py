"""第四层：ICM 独立筹码模型的正确性测试。

参考实现：全排列暴力枚举（淘汰顺序 = 一个排列，概率 = 逐步淘汰概率的乘积）。
"""

import random

from backend.icm import ICMError, icm_equities


def _brute_icm(stacks, payouts):
    """参考实现：递归从冠军端落位（与 Harville 定义一致），枚举全部落位排列。"""
    n = len(stacks)
    eq = [0.0] * n

    def rec(seated, p):
        if len(seated) == n:
            return
        place = len(seated) + 1
        prize = payouts[place - 1] if place - 1 < len(payouts) else 0.0
        tot = sum(stacks[i] for i in range(n) if i not in seated)
        for i in range(n):
            if i in seated:
                continue
            eq[i] += p * stacks[i] / tot * prize
            rec(seated | {i}, p * stacks[i] / tot)

    rec(set(), 1.0)
    return eq


def test_matches_bruteforce_permutations():
    rng = random.Random(42)
    for n in range(2, 7):
        for _ in range(6):
            stacks = [rng.randint(1, 200) for _ in range(n)]
            payouts = [rng.randint(0, 100) for _ in range(rng.randint(1, n))]
            got = icm_equities(stacks, payouts)
            exp = _brute_icm(stacks, payouts)
            for a, b in zip(got, exp):
                assert abs(a - b) < 1e-6, (stacks, payouts, got, exp)


def test_equities_sum_to_prize_pool():
    rng = random.Random(7)
    for _ in range(10):
        stacks = [rng.randint(1, 500) for _ in range(6)]
        payouts = [1000, 600, 400]
        eq = icm_equities(stacks, payouts)
        assert abs(sum(eq) - 2000) < 1e-6
        assert all(e >= 0 for e in eq)


def test_winner_take_all_is_chip_share():
    stacks = [3000, 2000, 1000]
    eq = icm_equities(stacks, [600])
    assert eq == [300.0, 200.0, 100.0]


def test_two_players_closed_form():
    # 两人 ICM 有解析解：eq_i = chip_i/total × P1 + chip_j/total × P2
    a, b, p1, p2 = 2000.0, 8000.0, 700.0, 300.0
    eq = icm_equities([a, b], [p1, p2])
    assert abs(eq[0] - (a / (a + b) * p1 + b / (a + b) * p2)) < 1e-9
    assert abs(eq[1] - (b / (a + b) * p1 + a / (a + b) * p2)) < 1e-9


def test_bigger_stack_never_lower_equity():
    stacks = [1000, 4500, 2000, 2500]
    payouts = [500, 300, 200, 100]
    eq = icm_equities(stacks, payouts)
    assert eq[1] == max(eq)          # 4500 最大
    assert eq[0] == min(eq)          # 1000 最小


def test_invalid_inputs_raise():
    try:
        icm_equities([100], [100])
        assert False
    except ICMError:
        pass
    try:
        icm_equities([100, 0], [100])
        assert False
    except ICMError:
        pass
    try:
        icm_equities([100, 100], [])
        assert False
    except ICMError:
        pass
    try:
        icm_equities([100, 100], [50, -1])
        assert False
    except ICMError:
        pass
