"""审查报告 02/03 的契约测试：CFR 终值权益域与金额口径。

02：桶平均是权益期望（eq ∈ [0,1]），不是胜负符号——混合桶（部分胜
部分负）必须按期望算 EV。老代码把桶平均符号 -0.2 喂给 ±1 终值，
40% 权益的 K 高牌被解成必败全弃。
03：骨架金额三路分离——双方累计投入（含前街）/第三方死钱/身后剩余
筹码。老代码把"身后剩余筹码"当总筹码、再减去含前街的已投入，全下
额度被重复扣减（各 10BB 在池、身后 10BB 时会解出"无法下注"）。
"""

from backend.cfr import solve_river, solve_street

BOARD = ["2c", "3d", "7h", "9c", "Td"]


def _spot():
    hero = [("Ks", "Kh")]
    vil = [("As", "Ad"), ("As", "Ah"), ("As", "Ac"),
           ("Ad", "Ah"), ("Ad", "Ac"), ("Ah", "Ac"),
           ("Qs", "Qd"), ("Qs", "Qh"), ("Qd", "Qh"), ("Qs", "Qc")]
    return hero, vil


def test_mixed_bucket_uses_equity_expectation():
    """02 一桶混合案例：40% 权益、跟 1 进 2 池只需 33% → 必须以跟注为主。"""
    hero, vil = _spot()
    res = solve_river(BOARD, hero, vil, pot_bb=2, to_call_bb=1, stack_bb=1.5,
                      buckets=1, iterations=1600)
    assert 0.35 <= res["hero_strength"][0] <= 0.45        # eq 域 [0,1]，非 ±1 符号
    strat = res["avg_strategy"][(0, 0, "")]
    assert strat["call"] > 0.9, strat                     # 老代码 fold ≈ 100%


def test_default_buckets_weak_value_calls_instead_of_folding():
    """02 默认 8 桶：KK 在 569JT 上对超对+高牙组合 41.7% 权益、跟注需
    38.5% → 跟注应压倒弃牌（老代码 fold=96.7%）。"""
    from backend.ranges import expand_range
    board = ["5h", "7s", "9c", "Jd", "3c"]
    vil = (expand_range("QQ") + expand_range("TT")[:4]
           + [c for c in expand_range("AA,JJ,99,77")
              if not (set(c) & set(board))][:14])
    assert len(vil) == 24                                  # 与报告复现场景一致
    res = solve_river(board, [("Kc", "Kd")], vil,
                      pot_bb=1.6, to_call_bb=1, stack_bb=1,
                      buckets=8, iterations=2000)
    strat = res["avg_strategy"][(0, res["hero_bucket"][0], "")]
    assert strat["call"] > strat["fold"], strat


def test_money_semantics_chips_behind_allow_betting():
    """03：双方各 10BB 已在池、身后还剩 10BB → 必须仍能下注。
    老代码口径：stack=10 减 committed=10 → extra=0，只剩过牌。"""
    hero, vil = _spot()
    res = solve_river(BOARD, hero, vil, pot_bb=20, to_call_bb=0, stack_bb=10,
                      money=(10, 10, 0, 10, 10), bet_sizes=(1.0,), iterations=100)
    assert "check" in res["root_labels"]
    assert any(l == "bet" or l.startswith("bet:") for l in res["root_labels"]), \
        res["root_labels"]


def test_money_semantics_dead_money_counts_into_pot():
    """03：第三方死钱计入底池——(10,10,5) 表示池 25，下注尺度按 25 算。"""
    hero, vil = _spot()
    res = solve_river(BOARD, hero, vil,
                      money=(10, 10, 5, 10, 10), bet_sizes=(1.0,), iterations=100)
    assert any(l == "bet" or l.startswith("bet:") for l in res["root_labels"]), \
        res["root_labels"]


def test_default_derivation_matches_explicit_money():
    """兼容：默认 (pot, to_call, stack) 派生与显式 money 元组结果逐位一致。"""
    hero, vil = _spot()
    kw = dict(bet_sizes=(1.0,), buckets=4, iterations=300)
    a = solve_river(BOARD, hero, vil, pot_bb=2, to_call_bb=1, stack_bb=1.5, **kw)
    b = solve_river(BOARD, hero, vil, money=(0.5, 1.5, 0.0, 1.0, 0.0), **kw)
    assert a["avg_strategy"] == b["avg_strategy"]
    assert a["root_labels"] == b["root_labels"]


def test_street_river_path_accepts_money():
    """solve_street 的河牌路径同样接受 money 并与默认派生一致。"""
    hero, vil = _spot()
    a = solve_street(BOARD, hero, vil, pot_bb=2, to_call_bb=1, stack_bb=1.5,
                     buckets=4, iterations=300)
    b = solve_street(BOARD, hero, vil, money=(0.5, 1.5, 0.0, 1.0, 0.0),
                     buckets=4, iterations=300)
    assert a["avg_strategy"] == b["avg_strategy"]


def test_invalid_money_rejected():
    hero, vil = _spot()
    for bad in ((1, 2, 0, 0), (1, 2, 0, 0, 0, 9), (-1, 2, 0, 5, 5),
                (1, 2, -3, 5, 5)):
        try:
            solve_river(BOARD, hero, vil, money=bad)
            assert False, bad
        except Exception as exc:
            assert "money" in str(exc) or "为负" in str(exc), (bad, exc)
