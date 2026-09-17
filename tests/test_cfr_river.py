"""第三层河牌求解器：用真实德扑组合 + treys 牌力复现 AKQ 定理频率。

构造：空白河牌（无 A/2/K），英雄范围 = AA(坚果) + 22(空气) 各 6 组合，
对手范围 = KK(抓诈) 6 组合——与 AKQ 教科书局同构。
定理：空气下注频率 = B/(P+B)，抓诈方跟注频率 = P/(P+B)，与下注尺度无关地成立。
"""

import time

import pytest

from backend.cfr import CFRError, solve_river
from backend.ranges import expand_range

BOARD = ["5h", "7s", "9c", "Jd", "3c"]  # 无 A/2/K 的空白河牌

_AA = [("Ac", "Ad"), ("Ac", "Ah"), ("Ac", "As"), ("Ad", "Ah"), ("Ad", "As"), ("Ah", "As")]
_22 = [("2c", "2d"), ("2c", "2h"), ("2c", "2s"), ("2d", "2h"), ("2d", "2s"), ("2h", "2s")]
_KK = [("Kc", "Kd"), ("Kc", "Kh"), ("Kc", "Ks"), ("Kd", "Kh"), ("Kd", "Ks"), ("Kh", "Ks")]


def _spot():
    return _AA + _22, _KK


def _hero_strat(res, combo):
    idx = res["hero_combos"].index(tuple(combo))
    b = res["hero_bucket"][idx]
    return res["avg_strategy"][(0, b, "")]


def _villain_strat(res, label):
    b = res["villain_bucket"][0]
    return res["avg_strategy"][(1, b, label)]


@pytest.mark.parametrize("pot,bet,q_exp,call_exp", [
    (1.0, 1.0, 0.5, 0.5),      # 底池注
    (1.0, 0.5, 1 / 3, 2 / 3),  # 半池注
    (1.0, 2.0, 2 / 3, 1 / 3),  # 超池注
])
def test_akq_shaped_river_matches_theorem(pot, bet, q_exp, call_exp):
    hero, villain = _spot()
    res = solve_river(BOARD, hero, villain, pot_bb=pot, bet_sizes=(bet,), iterations=3000)
    air = _hero_strat(res, ["2c", "2d"])
    nut = _hero_strat(res, ["Ac", "Ad"])
    call = _villain_strat(res, "b")
    assert nut["bet"] > 0.97, nut                        # 坚果必下注
    assert abs(air["bet"] - q_exp) < 0.05, (air, q_exp)  # 空气诈唬频率 = B/(P+B)
    assert abs(call["call"] - call_exp) < 0.05, (call, call_exp)
    # 对手弃牌与跟注互补
    assert abs(call["fold"] + call["call"] - 1.0) < 1e-9


def test_bucketing_by_range_equity():
    hero, villain = _spot()
    res = solve_river(BOARD, hero, villain, pot_bb=1.0, iterations=300, buckets=2)
    idx_aa = res["hero_combos"].index(("Ac", "Ad"))
    idx_22 = res["hero_combos"].index(("2c", "2d"))
    assert res["hero_strength"][idx_aa] == pytest.approx(1.0)   # AA 赢全部 KK → eq=1
    assert res["hero_strength"][idx_22] == pytest.approx(0.0)   # 22 输全部 KK → eq=0
    assert res["hero_bucket"][idx_aa] < res["hero_bucket"][idx_22]
    assert res["villain_strength"][0] == pytest.approx(0.5)     # KK 对 AA/22 各半 → eq=0.5
    assert res["pairs"] == 4  # 英雄 2 桶 × 对手 2 桶（等强组合均分），远小于 72 个原始组合对


def test_facing_bet_pot_odds_behavior():
    hero, villain = _spot()
    kk = [("Kc", "Kd")]
    # 对手极化范围（AA+22 各半）下注一池（pot=2 含其下注）：KK 权益 50% > 所需 33% → 必跟
    res = solve_river(BOARD, kk, hero, pot_bb=2.0, to_call_bb=1.0, iterations=3000)
    strat = res["avg_strategy"][(0, res["hero_bucket"][0], "")]
    assert strat["call"] > 0.95, strat
    # 对手只有坚果（AA）：KK 权益 0% < 所需 → 必弃
    res2 = solve_river(BOARD, kk, _AA, pot_bb=2.0, to_call_bb=1.0, iterations=3000)
    strat2 = res2["avg_strategy"][(0, res2["hero_bucket"][0], "")]
    assert strat2["fold"] > 0.95, strat2


def test_allin_clamp_and_fold():
    hero, villain = _spot()
    # 筹码只够一个底池注：AA 全下（坚果必下），22 按定理半数诈唬；
    # KK 对"含 GTO 诈唬比例的下注范围"恰好 50% 权益 → 跟弃各半（B/(P+B) = 1/2）
    res = solve_river(BOARD, hero, villain, pot_bb=1.0, stack_bb=1.5,
                      bet_sizes=(1.0,), iterations=3000)
    nut = _hero_strat(res, ["Ac", "Ad"])
    assert nut["bet"] > 0.95, nut
    air = _hero_strat(res, ["2c", "2d"])
    assert abs(air["bet"] - 0.5) < 0.05, air
    kk = _villain_strat(res, "b")
    assert abs(kk["fold"] - 0.5) < 0.05, kk


def test_determinism_and_multi_sizes():
    hero, villain = _spot()
    kw = dict(pot_bb=1.0, bet_sizes=(0.5, 1.0), iterations=800)
    r1 = solve_river(BOARD, hero, villain, **kw)
    r2 = solve_river(BOARD, hero, villain, **kw)
    assert r1["avg_strategy"] == r2["avg_strategy"]  # 同参数完全可复现
    root = r1["avg_strategy"][(0, r1["hero_bucket"][0], "")]
    labels = set(r1["root_labels"])
    assert {"check", "bet:P×0.5", "bet:P×1"} <= labels
    assert abs(sum(root.values()) - 1.0) < 1e-9
    assert all(0.0 <= p <= 1.0 for p in root.values())


def test_error_paths():
    hero, villain = _spot()
    with pytest.raises(CFRError):  # 河牌必须 5 张
        solve_river(BOARD[:4], hero, villain, pot_bb=1.0)
    with pytest.raises(CFRError):  # 死牌过滤后为空
        solve_river(BOARD, [("5h", "7s")], villain, pot_bb=1.0)  # 两张都在公共牌上


def test_typical_spot_latency():
    hero = expand_range("TT+, AJs+, KQs, AQo")       # 约 70 组合
    villain = expand_range("77+, AJs+, KQs, AQo, KJs, QJs")
    t0 = time.monotonic()
    res = solve_river(BOARD, hero, villain, pot_bb=8.0, to_call_bb=0.0,
                      iterations=1600)
    elapsed = time.monotonic() - t0
    assert elapsed < 12.0, elapsed  # CI 宽松上限；推演脚本另测分布
    root = res["avg_strategy"][(0, res["hero_bucket"][0], "")]
    assert abs(sum(root.values()) - 1.0) < 1e-9
