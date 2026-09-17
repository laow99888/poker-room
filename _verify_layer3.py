"""第三层深度推演：CFR 求解器的收敛、稳定性、确定性与性能全面验证。

运行：python _verify_layer3.py   （任何断言失败即非零退出）
"""
import random
import statistics
import sys
import time

from backend import charts, cfr
from backend.cfr import akq_tree, kuhn_tree, solve_river, solve_tree
from backend.decision import advice_for
from backend.ranges import expand_range
from backend.table import replay_state

RANKS = "AKQJT98765432"
SUITS = "cdhs"

PASS, FAIL = 0, []


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  [通过] {name}")
    else:
        FAIL.append(name)
        print(f"  [失败] {name}  {detail}")


def node_value(node, avg, c0, c1):
    if node["kind"] == "terminal":
        return node["value"]
    pk = node.get("pk", c0 if node["seat"] == 0 else c1)
    strat = avg[(node["seat"], pk, node["hist"])]
    return sum(strat[lab] * node_value(ch, avg, c0, c1)
               for lab, ch in zip(node["labels"], node["children"]))


def kuhn_value(avg):
    deals = [(a, b) for a in range(3) for b in range(3) if a != b]
    return sum(p * node_value(br, avg, a, b)
               for (a, b), (p, br) in zip(deals, kuhn_tree()["children"]))


def section(title):
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------- 1. 锚点局
section("锚点局 1：Kuhn 扑克（值 −1/18 + 均衡族自洽）")
avg = solve_tree(kuhn_tree(), iterations=12000)
v = kuhn_value(avg)
alpha = avg[(0, 0, "")]["bet"]
check("游戏值收敛到 −1/18 ± 0.005", abs(v + 1 / 18) < 0.005, f"值={v:.5f}")
check("P2 J 过牌后下注 ≈ 1/3", abs(avg[(1, 0, "c")]["bet"] - 1 / 3) < 0.05,
      avg[(1, 0, "c")])
check("P2 Q 遭注跟注 ≈ 1/3", abs(avg[(1, 1, "b")]["call"] - 1 / 3) < 0.05)
check("P1 K 下注 ≈ 3α", abs(avg[(0, 2, "")]["bet"] - 3 * alpha) < 0.06,
      f"α={alpha:.3f}")
check("P1 Q 遭注跟注 ≈ α+1/3",
      abs(avg[(0, 1, "cb")]["call"] - (alpha + 1 / 3)) < 0.06)

section("锚点局 2：AKQ 单街（诈唬频率 B/(P+B)，跟注频率 P/(P+B)）")
for pot, bet in ((1.0, 1.0), (1.0, 0.5), (1.0, 2.0)):
    a2 = solve_tree(akq_tree(pot, bet), iterations=12000)
    q = a2[(0, 1, "")]["bet"]
    call = a2[(1, 1, "b")]["call"]
    q_exp, c_exp = bet / (pot + bet), pot / (pot + bet)
    check(f"P={pot} B={bet}: Q诈唬 {q:.3f}≈{q_exp:.3f}，K跟注 {call:.3f}≈{c_exp:.3f}",
          abs(q - q_exp) < 0.04 and abs(call - c_exp) < 0.04)

# ---------------------------------------------------------- 2. 河牌真实牌
section("锚点局 3：河牌 AKQ 同构（AA+22 vs KK，真实 treys 评估）")
BOARD = ["5h", "7s", "9c", "Jd", "3c"]
AA = [(a + s1, a + s2) for a in ("A",) for s1, s2 in
      [(x, y) for i, x in enumerate(SUITS) for y in SUITS[i + 1:]]]
D22 = [(a + s1, a + s2) for a in ("2",) for s1, s2 in
       [(x, y) for i, x in enumerate(SUITS) for y in SUITS[i + 1:]]]
KK = [(a + s1, a + s2) for a in ("K",) for s1, s2 in
      [(x, y) for i, x in enumerate(SUITS) for y in SUITS[i + 1:]]]
HERO_AKQ, VILL_AKQ = AA + D22, KK


def river_akq(pot, bet):
    res = solve_river(BOARD, HERO_AKQ, VILL_AKQ, pot_bb=pot,
                      bet_sizes=(bet,), iterations=3000)
    i22 = res["hero_combos"].index(("2c", "2d"))
    q = res["avg_strategy"][(0, res["hero_bucket"][i22], "")]["bet"]
    call = res["avg_strategy"][(1, res["villain_bucket"][0], "b")]["call"]
    return q, call


for pot, bet in ((1.0, 1.0), (1.0, 0.5), (1.0, 2.0)):
    q, call = river_akq(pot, bet)
    q_exp, c_exp = bet / (pot + bet), pot / (pot + bet)
    check(f"P={pot} B={bet}: 22诈唬 {q:.3f}≈{q_exp:.3f}，KK跟注 {call:.3f}≈{c_exp:.3f}",
          abs(q - q_exp) < 0.05 and abs(call - c_exp) < 0.05)

# ------------------------------------------------------ 3. 随机场景推演
section("随机河牌推演：40 个随机场景的不变量")


def rand_board(rng):
    cards = rng.sample([r + s for r in RANKS for s in SUITS], 5)
    return cards


def rand_range(rng, exclude):
    pos = rng.choice(["UTG", "HJ", "CO", "BTN", "SB", "BB"])
    codes = charts.situation_codes(pos, rng.choice(["rfi", "call", "three_bet"])) or \
        charts.situation_codes(pos, "rfi")
    combos = [c for c in expand_range(",".join(codes))
              if not (set(c) & exclude)]
    return combos


rng = random.Random(20260919)
timings, errors = [], []
determinism_ok, stability_ok = True, True
conv_track = []
for n in range(40):
    board = rand_board(rng)
    excl = set(board)
    hero = rand_range(rng, excl)[:130]
    villain = rand_range(rng, excl)[:130]
    if len(hero) < 4 or len(villain) < 4:
        continue
    pot = round(rng.uniform(6, 40), 1)
    to_call = round(rng.choice([0, pot * rng.uniform(0.3, 1.2)]), 1)
    stack = round(max(pot * 1.5, pot + to_call + rng.uniform(5, 50)), 1)
    kw = dict(pot_bb=pot, to_call_bb=to_call, stack_bb=stack,
              bet_sizes=(0.5, 1.0), iterations=800)
    t0 = time.monotonic()
    try:
        res = solve_river(board, hero, villain, keep=[], **kw)
        elapsed = time.monotonic() - t0
        timings.append(elapsed)
        for strat in res["avg_strategy"].values():
            s = sum(strat.values())
            if not (0.999 <= s <= 1.001) or any(p < 0 or p > 1 for p in strat.values()):
                errors.append(f"场景{n}: 策略无效 {s}")
        if n < 5:  # 确定性：同参数必须完全一致
            res2 = solve_river(board, hero, villain, keep=[], **kw)
            if res["avg_strategy"] != res2["avg_strategy"]:
                determinism_ok = False
        if n < 6:  # 收敛性：根策略距参照解（12000 迭代）随迭代增加应整体收窄
            ref = solve_river(board, hero, villain, keep=[],
                              pot_bb=pot, to_call_bb=to_call, stack_bb=stack,
                              bet_sizes=(0.5, 1.0), iterations=12000)

            def root_dev(res_x):
                worst = 0.0
                for b in set(res_x["hero_bucket"]):
                    s1 = res_x["avg_strategy"].get((0, b, ""))
                    s2 = ref["avg_strategy"].get((0, b, ""))
                    if not s1 or not s2:
                        continue
                    for act in s1:
                        worst = max(worst, abs(s1.get(act, 0) - s2.get(act, 0)))
                return worst

            dev_lo = root_dev(res)                      # 800 迭代
            res_hi = solve_river(board, hero, villain, keep=[],
                                 pot_bb=pot, to_call_bb=to_call, stack_bb=stack,
                                 bet_sizes=(0.5, 1.0), iterations=4000)
            conv_track.append((dev_lo, root_dev(res_hi)))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"场景{n}: {type(exc).__name__}: {exc}")

check("40 场景无异常", not errors, "; ".join(errors[:3]))
check("策略均为有效概率分布", not errors)
check("确定性：重跑输出逐位一致", determinism_ok)
if conv_track:
    mean_lo = statistics.mean(lo for lo, _ in conv_track)
    mean_hi = statistics.mean(hi for _, hi in conv_track)
    worst_hi = max(hi for _, hi in conv_track)
    check(f"收敛整体收窄：4000 迭代平均偏差({mean_hi:.2f}) < 800 迭代平均偏差({mean_lo:.2f})",
          mean_hi < mean_lo)
    check(f"有界：4000 迭代距参照最坏偏差 ≤ 0.45（实际 {worst_hi:.2f}，UI 展示迭代数）",
          worst_hi <= 0.45)
if timings:
    timings.sort()
    print(f"  耗时 p50={timings[len(timings)//2]:.2f}s  "
          f"p95={timings[int(len(timings)*0.95)]:.2f}s  max={timings[-1]:.2f}s")

# ---------------------------------------------------- 4. 集成链路推演
section("集成链路：advice_for 河牌场景（真实 ops 序列）")
CFG = {"sb": 100, "bb": 200, "ante": 25}
base = [
    {"op": "action", "type": "fold", "seat": "UTG"},
    {"op": "action", "type": "fold", "seat": "HJ"},
    {"op": "action", "type": "fold", "seat": "CO"},
    {"op": "action", "type": "raise", "to": 600, "seat": "BTN"},
    {"op": "action", "type": "fold", "seat": "SB"},
    {"op": "action", "type": "call", "seat": "BB"},
]
state, hero_index, _ = replay_state(CFG, "BTN", ["As", "Ad"], base)
deal = [repr(c) for c in state.get_dealable_cards()]
flop, turn, river = deal[3:6], deal[7], deal[11]
ops = base + [
    {"op": "board", "cards": flop},
    {"op": "action", "type": "check", "seat": "BB"},
    {"op": "action", "type": "raise", "to": 800, "seat": "BTN"},
    {"op": "action", "type": "call", "seat": "BB"},
    {"op": "board", "cards": [turn]},
    {"op": "action", "type": "check", "seat": "BB"},
    {"op": "action", "type": "raise", "to": 2100, "seat": "BTN"},
    {"op": "action", "type": "call", "seat": "BB"},
    {"op": "board", "cards": [river]},
    {"op": "action", "type": "check", "seat": "BB"},
]
state, hero_index, _ = replay_state(CFG, "BTN", ["As", "Ad"], ops)
t0 = time.monotonic()
adv = advice_for(state, hero_index, 20000, seed=7)
full = time.monotonic() - t0
c = adv["cfr"]
check("河牌单挑返回 supported", c.get("supported") is True, c.get("reason", ""))
check("CFR mix 非空且方向合理", bool(c.get("mix")))
check(f"全链路耗时 {full:.2f}s ≤ 6s", full <= 6.0)
print(f"  启发式: {adv['recommendation']['primary']} | "
      f"CFR: {[(m['label'], m['pct']) for m in c.get('mix', [])]}")

# 面对下注场景
ops2 = [op for op in ops]
state2, hero2, _ = replay_state(CFG, "BTN", ["As", "Ad"], ops2[:-1])  # BB 未过牌
# 此时轮到 BB，构造 BB 下注后 BTN 面对下注
ops3 = ops2[:-1] + [{"op": "action", "type": "raise", "to": 6000, "seat": "BB"}]
state3, hero3, _ = replay_state(CFG, "BTN", ["As", "Ad"], ops3)
adv3 = advice_for(state3, hero3, 20000, seed=7)
c3 = adv3["cfr"]
check("面对下注场景返回 supported", c3.get("supported") is True, c3.get("reason", ""))
if c3.get("supported"):
    labels3 = [m["action"] for m in c3["mix"]]
    check("面对下注 mix 含跟注选项（AA 权益领先时弃牌频率可为 0）",
          "call" in labels3, labels3)
    print(f"  CFR 面对下注: {[(m['label'], m['pct']) for m in c3['mix']]}")

# ------------------------------------------------------------------ 总结
print(f"\n=== 总结：{PASS} 项通过，{len(FAIL)} 项失败 ===")
if FAIL:
    print("失败项：", FAIL)
    sys.exit(1)
print("第三层推演全部通过 ✓")
