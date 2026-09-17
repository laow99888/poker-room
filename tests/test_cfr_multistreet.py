"""第四层：翻牌/转牌多街求解的正确性测试。

锚点策略：
  1. 河牌回归恒等式——solve_street(5 张) 与 solve_river 输出逐位一致；
  2. 转牌全枚举对账——抽样上限 ≥ 剩余组合数时 rollout 精确，
     与测试端独立枚举（同一死牌约定）逐位一致；
  3. 行为不变量——强度排序合理、频率合法、确定性、近似标记。
"""

from itertools import combinations as nCr

from treys import Card as TCard, Evaluator

from backend.cfr import solve_river, solve_street

BOARD4 = ["Qh", "7d", "9c", "2d"]
HERO = [("9h", "9d"), ("Ah", "Kd"), ("As", "Ks"), ("Qs", "Qd"), ("Td", "Jd")]
VILLAIN = [("Kc", "Kh"), ("Qh", "Qs"), ("Jc", "Th"), ("8c", "8d"), ("Ad", "Kd")]

_EVAL = Evaluator()
_T = {r + s: TCard.new(r + s) for s in "shdc" for r in "AKQJT98765432"}


def _true_eq(hc, vc, board):
    """测试端独立枚举：发完剩余公共牌的胜率（平局 0.5）。

    死牌约定与求解器一致：只排除公共牌与这一对组合。
    """
    need = 5 - len(board)
    dead = set(board) | set(hc) | set(vc)
    deck = [r + s for s in "shdc" for r in "AKQJT98765432" if r + s not in dead]
    runs = list(nCr(deck, need))
    wins = 0.0
    for run in runs:
        b = [_T[c] for c in board + list(run)]
        hr = _EVAL.evaluate(b, [_T[hc[0]], _T[hc[1]]])
        vr = _EVAL.evaluate(b, [_T[vc[0]], _T[vc[1]]])
        wins += 1.0 if hr < vr else (0.0 if hr > vr else 0.5)
    return wins / len(runs)


def test_river_dispatch_identity():
    board = BOARD4 + ["Ks"]
    kw = dict(pot_bb=2.0, bet_sizes=(0.5, 1.0), iterations=300)
    a = solve_river(board, HERO, VILLAIN, **kw)
    b = solve_street(board, HERO, VILLAIN, **kw)
    assert a["avg_strategy"] == b["avg_strategy"]
    assert a["root_labels"] == b["root_labels"]
    assert a["pairs"] == b["pairs"]


def test_turn_exact_enumeration_matches_independent_reference():
    # 抽样上限 ≥ C(deck,1)=28 → 全枚举，hero_strength 应与独立枚举逐位一致
    res = solve_street(BOARD4, HERO, VILLAIN, pot_bb=2.0, iterations=50,
                       probe_runouts=64, pair_runouts=64, pair_samples=10)
    for i, hc in enumerate(res["hero_combos"]):
        vals = [_true_eq(hc, vc, BOARD4) for vc in res["villain_combos"]
                if not (set(hc) & set(vc))]   # 与求解器一致：跳过冲突组合
        exp = sum(vals) / len(vals)
        assert abs(res["hero_strength"][i] - exp) < 1e-9, (hc, exp)


def test_turn_strength_ordering_is_sane():
    res = solve_street(BOARD4, HERO, VILLAIN, pot_bb=2.0, iterations=50,
                       probe_runouts=64, pair_runouts=64, pair_samples=10)
    by_combo = dict(zip(res["hero_combos"], res["hero_strength"]))
    assert by_combo[("9h", "9d")] > 0.9          # 99 在 Q792 上是暗三
    assert by_combo[("Qs", "Qd")] > 0.9          # QQ 顶暗三
    assert by_combo[("As", "Ks")] < 0.45         # AK 高牌
    assert by_combo[("9h", "9d")] == max(res["hero_strength"])


def test_flop_invariants_and_determinism():
    kw = dict(pot_bb=2.0, bet_sizes=(0.5, 1.0), iterations=200)
    a = solve_street(BOARD4[:3], HERO, VILLAIN, **kw)
    assert a["approximate"] is True
    assert a["pairs"] >= 5
    for strat in a["avg_strategy"].values():
        total = sum(strat.values())
        assert 0.99 <= total <= 1.01
        assert all(0.0 <= p <= 1.0 for p in strat.values())
    b = solve_street(BOARD4[:3], HERO, VILLAIN, **kw)
    assert a["avg_strategy"] == b["avg_strategy"]   # 固定种子 → 逐位可复现


def test_turn_facing_bet_and_allin():
    res = solve_street(BOARD4, HERO, VILLAIN, pot_bb=6.0, to_call_bb=2.0,
                       stack_bb=8.0, bet_sizes=(1.0,), iterations=200,
                       probe_runouts=64, pair_runouts=64)
    assert res["pairs"] >= 5
    assert "fold" in res["root_labels"] and "raise" in res["root_labels"]


def test_invalid_board_sizes_raise():
    hero = [("As", "Ks")]
    vil = [("Qd", "Qh")]
    for n in (0, 1, 2, 6):
        board = (BOARD4 + ["Ks", "4h"])[:n]
        try:
            solve_street(board, hero, vil, pot_bb=1.0)
            assert False, n
        except Exception as exc:
            assert "3 张" in str(exc) or "4 张" in str(exc) or "5 张" in str(exc)
