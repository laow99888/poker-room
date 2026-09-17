# -*- coding: utf-8 -*-
"""第一层算法深度推演：精确枚举对照 + 收敛性 + 已知牌局对照 + 边界。"""
import warnings, random, itertools
warnings.filterwarnings("ignore")
from treys import Card as TCard, Evaluator
from backend.textures import analyze_board, range_equity, range_advantage, nut_advantage

ev = Evaluator()
fails = []
checked = 0

def ok(name, cond, detail=""):
    global checked
    checked += 1
    if not cond:
        fails.append(f"{name} {detail}")
    return cond

# ═══ 一、精确枚举对照（河牌：无抽样，全组合硬算）═══
def exact_river(hero_combos, villain_combos, board):
    """河牌圈精确范围权益：全枚举组合对，无任何抽样。"""
    board_t = [TCard.new(c) for c in board]
    dead = set(board)
    H = [h for h in hero_combos if h[0] not in dead and h[1] not in dead]
    V = [v for v in villain_combos if v[0] not in dead and v[1] not in dead]
    score = games = 0
    for h in H:
        ht = [TCard.new(h[0]), TCard.new(h[1])]
        for v in V:
            if v[0] in h or v[1] in h:
                continue
            vt = [TCard.new(v[0]), TCard.new(v[1])]
            hs = ev.evaluate(board_t, ht)
            vs = ev.evaluate(board_t, vt)
            games += 1
            score += 1.0 if hs < vs else (0.5 if hs == vs else 0.0)
    return score / games * 100 if games else None

river_board = ["Ah", "Kd", "2c", "9h", "5s"]
# 范围A = {AA 三种组合, 22 一种组合}，范围B = {KK 三种}（均避开公共牌）
hero_r = [("As", "Ad"), ("As", "Ac"), ("Ad", "Ac"), ("2h", "2s")]
villain_r = [("Ks", "Kc"), ("Ks", "Kh"), ("Kc", "Kh")]
exact = exact_river(hero_r, villain_r, river_board)
mc = range_equity(hero_r, villain_r, river_board, iterations=20000, seed=7)
ok("河牌精确枚举 vs MC（偏差≤1.5%）", abs(mc - exact) <= 1.5, f"exact={exact:.2f} mc={mc}")

# 换一个非平凡的河牌面（有同花可能）
river_board2 = ["Ah", "Kh", "Qh", "2c", "5s"]
hero_r2 = [("Ah", "Kh"), ("Qh", "Jh"), ("As", "Ad")]     # 两个同花听牌成牌组合 + AA
villain_r2 = [("Ks", "Kc"), ("Ks", "Kh"), ("Kc", "Kh")]  # KK 三种
exact2 = exact_river(hero_r2, villain_r2, river_board2)
mc2 = range_equity(hero_r2, villain_r2, river_board2, iterations=20000, seed=7)
ok("同花面精确 vs MC（偏差≤1.5%）", abs(mc2 - exact2) <= 1.5, f"exact={exact2:.2f} mc={mc2}")

# ═══ 二、转牌：枚举河牌 × MC 对照 ═══
def exact_turn(hero_combos, villain_combos, board4):
    """转牌精确枚举：按 matchup 逐对计算，河牌排除该对决的全部死牌。

    关键：每种 matchup 的可行河牌集合不同（双方底牌+公共牌之外的牌），
    与蒙特卡洛的条件化完全一致。
    """
    dead_board = set(board4)
    H = [h for h in hero_combos if h[0] not in dead_board and h[1] not in dead_board]
    V = [v for v in villain_combos if v[0] not in dead_board and v[1] not in dead_board]
    score = games = 0
    for h in H:
        for v in V:
            if v[0] in h or v[1] in h:
                continue
            dead_pair = dead_board | {h[0], h[1], v[0], v[1]}
            rivers = [c for c in ALL_CARDS if c not in dead_pair]
            ht = [TCard.new(h[0]), TCard.new(h[1])]
            vt = [TCard.new(v[0]), TCard.new(v[1])]
            for r_ in rivers:
                bt = board_t_for(board4, r_)
                hs = ev.evaluate(bt, ht)
                vs = ev.evaluate(bt, vt)
                games += 1
                score += 1.0 if hs < vs else (0.5 if hs == vs else 0.0)
    return score / games * 100 if games else None

def board_t_for(board4, river):
    return [TCard.new(c) for c in board4] + [TCard.new(river)]

ALL_CARDS = [r + s for s in "cdhs" for r in "AKQJT98765432"]
turn_board = ["Ah", "Kd", "2c", "9h"]
hero_t = [("As", "Ad"), ("As", "Ac"), ("Ad", "Ac")]
villain_t = [("Ks", "Kc"), ("Ks", "Kh"), ("Kc", "Kh")]
exact_t = exact_turn(hero_t, villain_t, turn_board)
mc_t = range_equity(hero_t, villain_t, turn_board, iterations=20000, seed=7)
ok("转牌枚举 vs MC（偏差≤1.5%）", abs(mc_t - exact_t) <= 1.5, f"exact={exact_t:.2f} mc={mc_t}")

# ═══ 三、翻牌前经典牌局已知值 ═══
known = [
    ("AA vs KK", [("As","Ad")], [("Ks","Kc")], 79.0, 85.0),
    ("AA vs 22", [("As","Ad")], [("2c","2d")], 77.0, 84.0),
]
for name, h, v, lo, hi in known:
    r = range_equity(h, v, [], iterations=50000, seed=11)
    ok(f"翻前已知值 {name}: {r}% ∈ [{lo},{hi}]", lo <= r <= hi)

# ═══ 四、蒙特卡洛收敛性 ═══
errs = []
for iters in (2000, 8000, 32000):
    r = range_equity([("As","Ad")], [("Ks","Kc")], [], iterations=iters, seed=99)
    errs.append(abs(r - 81.9))
ok("收敛性: 各档误差均在噪声带内(≤1.0)", max(errs) <= 1.0, f"errs={errs}")

# ═══ 五、确定性：同种子两次完全一致 ═══
a1 = range_equity(hero_r, villain_r, river_board, iterations=5000, seed=3)
a2 = range_equity(hero_r, villain_r, river_board, iterations=5000, seed=3)
ok("同种子结果完全一致", a1 == a2)

# ═══ 六、牌面结构属性表 ═══
cases = [
    (["Ah","Kh","Qh"], dict(monotone=True, label="湿润")),
    (["Ah","Kh","Qd"], dict(two_tone=True)),
    (["Kd","7c","2s"], dict(label="干旱")),
    (["7h","7d","2c"], dict(paired=True)),
    (["7h","7d","7c"], dict(trips=True, label="湿润")),
]
for board, expect in cases:
    res = analyze_board(board)
    for k, v in expect.items():
        ok(f"牌面{board} {k}=={v}", res[k] == v, f"实际 {res[k]}")

# ═══ 七、边界：空范围 / 空牌面 ═══
ok("空范围返回 None", range_equity([], [], [], 100) is None)

# ═══ 八、NA 方向性：成对公共牌抬高低牌范围的三条比例 ═══
na_paired = nut_advantage([("As","Ad")], [("2h","2s")], ["7h","7d","2c"], runouts=15, seed=5)
ok("对子面 NA 方向合理（2h2s 恒为葫芦 → villain=100）", na_paired["villain"] == 100.0)

print(f"共校验 {checked} 项")
if fails:
    print(f"失败 {len(fails)} 项:")
    for f_ in fails:
        print("  ✗", f_)
    raise SystemExit(1)   # 19：失败必须以非零码退出，CI/脚本才能感知
else:
    print("全部通过 ✓")
