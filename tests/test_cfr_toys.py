"""第三层锚点局：CFR 必须先收敛到教科书理论解，才允许接入建议链路。

Kuhn 扑克：先手理论游戏值 −1/18（先注单位），持 J 诈唬频率被强制为 1/3。
AKQ 单街：持 Q 诈唬频率 = B/(P+B)，抓诈方跟注频率 = P/(P+B)，与下注尺度无关地成立。
"""

from backend.cfr import akq_tree, kuhn_tree, solve_tree


def _node_value(node, avg, c0, c1):
    kind = node["kind"]
    if kind == "terminal":
        return node["value"]
    pk = node.get("pk", c0 if node["seat"] == 0 else c1)
    strat = avg[(node["seat"], pk, node["hist"])]
    return sum(strat[lab] * _node_value(ch, avg, c0, c1)
               for lab, ch in zip(node["labels"], node["children"]))


def _kuhn_value(avg):
    deals = [(c0, c1) for c0 in range(3) for c1 in range(3) if c0 != c1]
    children = kuhn_tree()["children"]
    total = 0.0
    for (c0, c1), (p, branch) in zip(deals, children):
        total += p * _node_value(branch, avg, c0, c1)
    return total


def _akq_value(avg, pot, bet):
    root = akq_tree(pot, bet)
    total = 0.0
    for c0 in (0, 1):  # 0=A, 1=Q
        branch = root["children"][c0][1]
        total += 0.5 * _node_value(branch, avg, c0, 0)
    return total


def _check_valid(avg):
    for iset, strat in avg.items():
        s = sum(strat.values())
        assert all(0.0 <= p <= 1.0 for p in strat.values()), f"频率越界：{iset}"
        assert abs(s - 1.0) < 1e-6, f"频率和 ≠ 1：{iset} sum={s}"


def test_kuhn_value_and_forced_bluff():
    # 均衡是一个族：P1 由 α∈[0,1/3] 刻画（J 下注 α，K 下注 3α，Q 遭注后跟注 α+1/3）；
    # P2 唯一：J 过牌后下注 1/3、遭注必弃，Q 过牌、遭注跟注 1/3，K 恒下注/跟注。
    avg = solve_tree(kuhn_tree(), iterations=8000)
    _check_valid(avg)
    assert abs(_kuhn_value(avg) - (-1 / 18)) < 0.01, _kuhn_value(avg)
    # P2 唯一均衡策略（强断言）
    assert abs(avg[(1, 0, "c")]["bet"] - 1 / 3) < 0.06      # J 诈唬
    assert avg[(1, 0, "b")]["fold"] > 0.97                   # J 遭注必弃
    assert avg[(1, 1, "c")]["check"] > 0.97                  # Q 过牌后手
    assert abs(avg[(1, 1, "b")]["call"] - 1 / 3) < 0.06      # Q 遭注跟注 1/3
    assert avg[(1, 2, "c")]["bet"] > 0.97                    # K 必下注
    assert avg[(1, 2, "b")]["call"] > 0.97                   # K 遭注必跟
    # P1 族结构：读出 α，其余频率必须与 3α / α+1/3 自洽
    alpha = avg[(0, 0, "")]["bet"]
    assert 0.02 < alpha < 0.36, alpha
    assert abs(avg[(0, 2, "")]["bet"] - 3 * alpha) < 0.08
    assert abs(avg[(0, 1, "cb")]["call"] - (alpha + 1 / 3)) < 0.08
    assert avg[(0, 1, "")]["bet"] < 0.05                     # Q 从不主动下注
    assert avg[(0, 0, "cb")]["fold"] > 0.97                  # J 遭注必弃


def test_akq_bluff_and_call_frequencies_by_bet_size():
    # (底池 P, 下注 B, 持Q诈唬频率 B/(P+B), 抓诈方跟注频率 P/(P+B))
    cases = [
        (1.0, 1.0, 0.5, 0.5),          # 底池注
        (1.0, 0.5, 1 / 3, 2 / 3),      # 半池注
        (1.0, 2.0, 2 / 3, 1 / 3),      # 超池注
    ]
    for pot, bet, q_exp, call_exp in cases:
        avg = solve_tree(akq_tree(pot, bet), iterations=8000)
        _check_valid(avg)
        a_bet = avg[(0, 0, "")]["bet"]           # A（坚果）永远下注
        q_bet = avg[(0, 1, "")]["bet"]           # Q（空气）诈唬
        call = avg[(1, 1, "b")]["call"]          # K（抓诈）跟注
        assert a_bet > 0.97, (pot, bet, a_bet)
        assert abs(q_bet - q_exp) < 0.05, (pot, bet, q_bet, q_exp)
        assert abs(call - call_exp) < 0.05, (pot, bet, call, call_exp)
        # 游戏值与解析公式核对：EV = [EV(A)+EV(Q)]/2（已投额记账，弃牌赢半个底池）
        half, cc = pot / 2, pot / 2 + bet
        ev_a = call * cc + (1 - call) * half
        ev_q = q_bet * (call * -cc + (1 - call) * half) + (1 - q_bet) * -half
        assert abs(_akq_value(avg, pot, bet) - (ev_a + ev_q) / 2) < 0.02


def test_kuhn_converges_from_different_iteration_counts():
    v1 = _kuhn_value(solve_tree(kuhn_tree(), iterations=2000))
    v2 = _kuhn_value(solve_tree(kuhn_tree(), iterations=8000))
    assert abs(v1 - (-1 / 18)) < 0.02
    assert abs(v2 - v1) < 0.015  # 迭代越多越稳，且都已在理论值附近
