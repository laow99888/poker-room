"""第一层算法测试：牌面结构 + 范围优势/坚果优势。"""
from backend.textures import analyze_board, range_advantage, nut_advantage, range_equity


def test_board_texture_labels():
    mono = analyze_board(["Ah", "Kh", "Qh"])          # 同花连张 → 湿润
    assert mono["monotone"] is True and mono["label"] == "湿润"
    dry = analyze_board(["Kd", "7c", "2s"])           # 彩虹高张 → 干旱
    assert dry["label"] == "干旱" and dry["connected"] == 0
    paired = analyze_board(["7h", "7d", "2c"])
    assert paired["paired"] is True


def test_range_advantage_known_matchup():
    # K72 彩虹面，对手 22 恰好组成四条 2（2s 在公共牌）→ AA 几乎必输
    ra = range_advantage([("As", "Ad")], [("2c", "2d")], ["Kh", "7c", "2s"],
                         iterations=4000, seed=1)
    assert ra["hero_eq"] < 25
    assert ra["adv"] < -50
    # 对照：把对手换成 33（无三条）→ AA 应大幅领先
    ra2 = range_advantage([("As", "Ad")], [("3c", "3d")], ["Kh", "7c", "2s"],
                          iterations=4000, seed=1)
    assert ra2["hero_eq"] > 80


def test_nut_advantage_fields():
    na = nut_advantage([("As", "Ad")], [("2c", "2d")], ["Kh", "7c", "2s"],
                       runouts=10, seed=1)
    assert set(na) == {"hero", "villain", "adv"}
    assert na["villain"] == 100.0     # 22 在 2s 在公共牌时恒为四条


def _exact_river(hero_combos, villain_combos, board):
    """独立精确枚举（不经过被测代码），用于交叉验证。"""
    from treys import Card as TCard, Evaluator
    ev = Evaluator()
    bt = [TCard.new(c) for c in board]
    dead = set(board)
    H = [h for h in hero_combos if h[0] not in dead and h[1] not in dead]
    V = [v for v in villain_combos if v[0] not in dead and v[1] not in dead]
    score = games = 0
    for h in H:
        for v in V:
            if v[0] in h or v[1] in h:
                continue
            hs = ev.evaluate(bt, [TCard.new(h[0]), TCard.new(h[1])])
            vs = ev.evaluate(bt, [TCard.new(v[0]), TCard.new(v[1])])
            games += 1
            score += 1.0 if hs < vs else (0.5 if hs == vs else 0.0)
    return score / games * 100 if games else None


def test_mc_matches_exact_enumeration_on_river():
    # MC（固定种子 20000 次）必须与精确枚举一致（±1.5%）
    board = ["Ah", "Kd", "2c", "9h", "5s"]
    hero = [("As", "Ad"), ("As", "Ac"), ("Ad", "Ac"), ("2h", "2s")]
    villain = [("Ks", "Kc"), ("Ks", "Kh"), ("Kc", "Kh")]
    exact = _exact_river(hero, villain, board)
    mc = range_equity(hero, villain, board, iterations=20000, seed=7)
    assert abs(mc - exact) <= 1.5


def test_range_advantage_direction_sanity():
    # AA 范围在 A 高牌面对 KK 范围应大幅领先（三条 A 压制一对 K）
    ra = range_advantage([("As", "Ad"), ("Ac", "Ah")], [("Ks", "Kc"), ("Kh", "Kd")],
                         ["Ah", "Kd", "2c", "9h", "5s"], iterations=4000, seed=3)
    assert ra["hero_eq"] > 75
    assert ra["adv"] > 50
