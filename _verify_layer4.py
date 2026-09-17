"""第四层深度推演：多街求解 + ICM 的自校验（失败即非零退出）。

覆盖：
  A. 河牌回归恒等式——solve_street(5 张) 与 solve_river 逐位一致；
  B. 教科书锚点仍成立——AKQ 河牌定理频率（回归不被第四层破坏）；
  C. 转牌全枚举对账——精确上限下与独立枚举逐位一致 + 强度方向合理；
  D. 确定性——同参数两次求解逐位一致；
  E. ICM——与暴力落位枚举对账、和=奖池、单调性、两人闭式解；
  F. 性能——真实规模转牌求解耗时上限。
"""

import random
import sys
import time
from itertools import permutations

from treys import Card as TCard, Evaluator

from backend.cfr import akq_tree, solve_river, solve_street, solve_tree
from backend.icm import icm_equities

sys.path.insert(0, ".")
PASS = 0


def ok(cond, msg):
    global PASS
    if not cond:
        print(f"  ✗ {msg}")
        sys.exit(1)
    PASS += 1


# ---------- A. 河牌回归恒等式 ----------
print("A. 河牌回归恒等式")
rng = random.Random(4)
for trial in range(6):
    deck = [r + s for s in "shdc" for r in "AKQJT98765432"]
    rng.shuffle(deck)
    board = deck[:5]
    rest = deck[5:]
    hero = [tuple(sorted(rest[i * 2:i * 2 + 2])) for i in range(4)]
    vil = [tuple(sorted(rest[8 + i * 2:8 + i * 2 + 2])) for i in range(4)]
    kw = dict(pot_bb=round(rng.uniform(1, 10), 2),
              to_call_bb=round(rng.choice([0, rng.uniform(0.5, 3)]), 2),
              bet_sizes=(0.5, 1.0), iterations=200)
    a = solve_river(board, hero, vil, **kw)
    b = solve_street(board, hero, vil, **kw)
    ok(a["avg_strategy"] == b["avg_strategy"], f"trial{trial} 策略不一致")
    ok(a["root_labels"] == b["root_labels"], f"trial{trial} 行动骨架不一致")
print("  6 组随机河牌场景全部逐位一致")

# ---------- B. AKQ 河牌锚点（真实牌） ----------
print("B. AKQ 河牌锚点（真实牌，定理频率）")
BOARD = ["5h", "7s", "9c", "Jd", "3c"]   # 空白河牌：A(坚果) > K > 2(空气)
AA = [("Ac", "Ad"), ("Ac", "Ah"), ("Ac", "As"), ("Ad", "Ah"), ("Ad", "As"), ("Ah", "As")]
LL = [("2c", "2d"), ("2c", "2h"), ("2c", "2s"), ("2d", "2h"), ("2d", "2s"), ("2h", "2s")]
KK = [("Kc", "Kd"), ("Kc", "Kh"), ("Kc", "Ks"), ("Kd", "Kh"), ("Kd", "Ks"), ("Kh", "Ks")]
for pot, bet in ((1.0, 0.5), (1.0, 1.0), (1.0, 2.0)):
    res = solve_river(BOARD, AA + LL, KK, pot_bb=pot,
                      bet_sizes=(bet,), iterations=3000)
    b_air = res["hero_bucket"][res["hero_combos"].index(("2c", "2s"))]
    b_vil = res["villain_bucket"][0]
    bluff = res["avg_strategy"][(0, b_air, "")].get("bet", 0.0)
    call = res["avg_strategy"][(1, b_vil, "b")].get("call", 0.0)
    ok(abs(bluff - bet / (pot + bet)) < 0.05,
       f"诈唬频率 {bluff:.3f} ≠ B/(P+B)={bet/(pot+bet):.3f}")
    ok(abs(call - pot / (pot + bet)) < 0.05,
       f"抓诈频率 {call:.3f} ≠ P/(P+B)={pot/(pot+bet):.3f}")
print("  三种下注尺度下定理频率全部吻合")

# ---------- C. 转牌全枚举对账 ----------
print("C. 转牌全枚举对账（独立参考实现）")
_T = {r + s: TCard.new(r + s) for s in "shdc" for r in "AKQJT98765432"}
_EV = Evaluator()


def true_eq(hc, vc, board):
    dead = set(board) | set(hc) | set(vc)
    deck = [r + s for s in "shdc" for r in "AKQJT98765432" if r + s not in dead]
    h = [_T[hc[0]], _T[hc[1]]]
    v = [_T[vc[0]], _T[vc[1]]]
    wins = 0.0
    for c in deck:
        b = [_T[x] for x in board] + [ _T[c] ]
        hr, vr = _EV.evaluate(b, h), _EV.evaluate(b, v)
        wins += 1.0 if hr < vr else (0.0 if hr > vr else 0.5)
    return wins / len(deck)


rng = random.Random(9)
for trial in range(5):
    deck = [r + s for s in "shdc" for r in "AKQJT98765432"]
    rng.shuffle(deck)
    board = deck[:4]
    rest = deck[4:]
    hero = [tuple(sorted(rest[i * 2:i * 2 + 2])) for i in range(4)]
    vil = [tuple(sorted(rest[8 + i * 2:8 + i * 2 + 2])) for i in range(4)]
    res = solve_street(board, hero, vil, pot_bb=3.0, iterations=100,
                       probe_runouts=64, pair_runouts=64, pair_samples=10)
    for i, hc in enumerate(res["hero_combos"]):
        vals = [true_eq(hc, vc, board) for vc in res["villain_combos"]
                if not (set(hc) & set(vc))]
        exp = sum(vals) / len(vals)
        ok(abs(res["hero_strength"][i] - exp) < 1e-9,
           f"trial{trial} {hc} 强度 {res['hero_strength'][i]} ≠ {exp}")
print("  5 组随机转牌场景逐位一致（全枚举精确）")

# ---------- D. 确定性 ----------
print("D. 确定性")
deck = [r + s for s in "shdc" for r in "AKQJT98765432"]
rng.shuffle(deck)
board, rest = deck[:4], deck[4:]
hero = [tuple(sorted(rest[i * 2:i * 2 + 2])) for i in range(5)]
vil = [tuple(sorted(rest[10 + i * 2:10 + i * 2 + 2])) for i in range(5)]
kw = dict(pot_bb=4.0, bet_sizes=(0.5, 1.0), iterations=300)
a = solve_street(board, hero, vil, **kw)
b = solve_street(board, hero, vil, **kw)
ok(a["avg_strategy"] == b["avg_strategy"], "两次求解结果不一致")

# ---------- E. ICM ----------
print("E. ICM 对账")


def brute_icm(stacks, payouts):
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


rng = random.Random(11)
for trial in range(30):
    n = rng.randint(2, 7)
    stacks = [rng.randint(1, 500) for _ in range(n)]
    payouts = [rng.randint(0, 100) for _ in range(rng.randint(1, n))]
    got = icm_equities(stacks, payouts)
    exp = brute_icm(stacks, payouts)
    ok(all(abs(x - y) < 1e-6 for x, y in zip(got, exp)),
       f"trial{trial} ICM 与暴力枚举不一致: {stacks} {payouts}")
    ok(abs(sum(got) - sum(payouts)) < 1e-6, "奖金和不相等")

a, b, p1, p2 = 2000.0, 8000.0, 700.0, 300.0
eq2 = icm_equities([a, b], [p1, p2])
ok(abs(eq2[0] - (a * p1 + b * p2) / (a + b)) < 1e-9, "两人闭式解不符")

rng = random.Random(13)
for _ in range(10):
    n = rng.randint(2, 8)
    stacks = [rng.randint(100, 1000) for _ in range(n)]
    payouts = sorted((rng.randint(50, 200) for _ in range(n)), reverse=True)
    eq = icm_equities(stacks, payouts)
    order = sorted(range(n), key=lambda i: -stacks[i])
    ok(eq[order[0]] >= eq[order[-1]], "筹码最多者奖金低于最少者（违反单调性）")
print("  30 组对账 + 闭式解 + 单调性全部通过")

# ---------- F. 性能 ----------
print("F. 性能预算")
from itertools import combinations as _nCr
big_deck = [r + s for s in "shdc" for r in "AKQJT98765432"]
rng.shuffle(big_deck)
board, rest = big_deck[:4], big_deck[4:]
pool = sorted(_nCr(rest, 2))            # 真实范围语义：组合间可共用牌面假设
rng.shuffle(pool)
hero = pool[:130]
vil = pool[130:260]
t0 = time.time()
res = solve_street(board, hero, vil, pot_bb=5.0, bet_sizes=(0.5, 1.0),
                   iterations=1600)
cost = time.time() - t0
ok(cost < 8.0, f"真实规模转牌求解耗时 {cost:.1f}s 超预算")
ok(res["hero_capped"] and res["villain_capped"], "大范围应触发抽样封顶")
print(f"  130×130 组合转牌求解 {cost:.2f}s（封顶后 {res['pairs']} 对桶）")

print(f"\n第四层推演全部通过（{PASS} 项断言）")
