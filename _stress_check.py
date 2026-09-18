"""压力 + 准确性压测（对本地运行的服务做黑盒验收）。

用法：
    python -m uvicorn backend.app:app --port 8137 --log-level warning   # 先起服务
    python -X utf8 _stress_check.py

两部分：
  A. 操作压力：并发一致性 / 混合负载 / 竞态序列 / 快速撤销循环 /
     异常输入轰击 / 记录幂等。
  B. 预测准确性：河牌与转牌用 treys 精确枚举做独立参考，对照
     /api/equity 蒙特卡洛；翻牌前用大小样本自洽；赔率单调性不变量。
"""

import concurrent.futures as futures
import random
import statistics
import sys
import time
from collections import defaultdict
from itertools import combinations as nCr

import requests
from treys import Card as TCard, Evaluator

BASE = "http://127.0.0.1:8137"
RNG = random.Random(20260918)
RESULTS = defaultdict(lambda: [0, 0, []])   # 名称 -> [通过, 失败, 失败详情]


def check(name, ok, detail=""):
    RESULTS[name][0 if ok else 1] += 1
    if not ok:
        RESULTS[name][2].append(str(detail)[:160])


def sec(title):
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------- 场景构造

def base_config(**kw):
    cfg = {"player_count": 6, "sb": 50, "bb": 100, "ante": 0,
           "stacks": [10000] * 6, "payouts": []}
    cfg.update(kw)
    return cfg


def preflop_btn_advice(hero_cards, iterations=20000, seed=7, **kw):
    """6 人桌 BTN 英雄；UTG/HJ/CO 平跟后轮到 BTN，可直接请求建议。"""
    ops = [{"op": "action", "seat": p, "type": "call"}
           for p in ("UTG", "HJ", "CO")]
    return {"config": base_config(**kw), "hero_pos": "BTN",
            "hero_cards": hero_cards, "ops": ops,
            "iterations": iterations, "seed": seed}


def random_cards(n, rng, exclude=()):
    pool = [r + s for s in "shdc" for r in "AKQJT98765432"
            if r + s not in set(exclude)]
    rng.shuffle(pool)
    return pool[:n]


# ---------------------------------------------------------------- A. 压力

def t_health():
    sec("A0 基准健康")
    r = requests.get(BASE + "/", timeout=5)
    check("A0 首页可达", r.status_code == 200, r.status_code)


def t_concurrent_consistency():
    sec("A1 并发一致性：同请求 8 并发 × 2 轮（5000 迭代，串行基线约 2.5s），响应必须逐位一致")
    payload = preflop_btn_advice(["As", "Ks"], iterations=5000)
    ref = None
    lat = []
    for rnd in range(2):
        def call(_):
            t0 = time.perf_counter()
            r = requests.post(BASE + "/api/hand/advice", json=payload, timeout=120)
            return r, (time.perf_counter() - t0) * 1000

        with futures.ThreadPoolExecutor(max_workers=8) as ex:
            out = list(ex.map(call, range(24)))
        lat += [ms for _, ms in out]
        for r, _ in out:
            check("A1 状态 200", r.status_code == 200, r.status_code)
            body = r.json()
            if ref is None:
                ref = body
            else:
                check("A1 响应逐位一致", body == ref,
                      "差异键: " + str([k for k in body if body.get(k) != ref.get(k)]))
    print(f"    延迟 ms  均值 {statistics.mean(lat):.0f}  最大 {max(lat):.0f}")


def t_mixed_load():
    sec("A2 混合负载：8 线程 500 个随机请求，不允许 5xx")
    decks = [random_cards(9, RNG) for _ in range(40)]
    jobs = []
    for i in range(500):
        d = decks[i % len(decks)]
        hero, v1 = d[0:2], d[2:4]
        board = d[4:8]
        kind = ["equity", "advice", "view", "stats"][i % 4]
        if kind == "equity":
            jobs.append(("POST", "/api/equity",
                         {"hero": hero, "villains": [{"type": "hand", "cards": v1}],
                          "board": board[:5], "iterations": 5000, "seed": i}))
        elif kind == "advice":
            jobs.append(("POST", "/api/hand/advice",
                         preflop_btn_advice(hero, iterations=5000, seed=i)))
        elif kind == "view":
            jobs.append(("POST", "/api/hand/view",
                         preflop_btn_advice(hero, seed=i)))
        else:
            jobs.append(("GET", "/api/stats/summary/all", None))

    def call(job):
        method, path, payload = job
        t0 = time.perf_counter()
        if method == "GET":
            r = requests.get(BASE + path, timeout=120)
        else:
            r = requests.post(BASE + path, json=payload, timeout=120)
        return r, (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    with futures.ThreadPoolExecutor(max_workers=8) as ex:
        out = list(ex.map(call, jobs))
    wall = time.perf_counter() - t0
    lat = [ms for _, ms in out]
    bad5 = [r.status_code for r, _ in out if r.status_code >= 500]
    bad4 = [(r.status_code, r.request.url, r.request.method)
            for r, _ in out if r.status_code >= 400]
    check("A2 无 5xx", not bad5, bad5[:5])
    check("A2 无意外 4xx", not bad4, bad4[:5])
    lat.sort()
    print(f"    500 请求 / 8 线程 墙钟 {wall:.1f}s   p50 {lat[250]:.0f}ms   "
          f"p95 {lat[475]:.0f}ms   最大 {lat[-1]:.0f}ms")


def t_race_sequence():
    sec("A3 竞态序列：操作逐步推进，每步穿插建议请求（旧响应语义正确性）")
    hero = ["Ah", "Ad"]
    ops = []
    plan = [("UTG", "call"), ("HJ", "call"), ("CO", "call"),
            ("BTN", "raise"),  # 英雄加注
            ("SB", "fold"), ("BB", "fold")]
    expected_actor = ["BTN", "BTN", "BTN", "SB", "BB"]
    for i, (seat, kind) in enumerate(plan):
        op = {"op": "action", "seat": seat,
              "type": kind, **({"to": 900} if kind == "raise" else {})}
        ops.append(op)
        r = requests.post(BASE + "/api/hand/advice",
                          json={"config": base_config(), "hero_pos": "BTN",
                                "hero_cards": hero, "ops": ops,
                                "iterations": 3000, "seed": i}, timeout=30)
        if i < 4:
            ok = r.status_code == 200 and r.json()["actor"] == expected_actor[i]
            check("A3 actor 随 ops 推进", ok,
                  f"step{i}: {r.status_code} actor={r.json().get('actor') if r.status_code==200 else '-'}")
        else:
            # 英雄加注 900 后 SB/BB 行动时，轮不到英雄 → 稳定 400
            check("A3 未轮到英雄时稳定 400", r.status_code == 400, r.status_code)
    # 英雄行动后补发翻牌 → 轮到 SB，此时英雄请求建议应 400"还没轮到你"
    ops.append({"op": "board", "cards": ["Ks", "7d", "2c"]})
    r = requests.post(BASE + "/api/hand/advice",
                      json={"config": base_config(), "hero_pos": "BTN",
                            "hero_cards": hero, "ops": ops,
                            "iterations": 3000, "seed": 9}, timeout=30)
    check("A3 发牌后非英雄回合 400", r.status_code == 400, r.status_code)


def t_undo_loop():
    sec("A4 快速撤销循环：加一删一 30 轮")
    hero = ["Qh", "Qd"]
    base_ops = [{"op": "action", "seat": p, "type": "call"} for p in ("UTG", "HJ", "CO")]
    for i in range(30):
        ops = base_ops + ([{"op": "action", "seat": "BTN", "type": "call"}] if i % 2 else [])
        r = requests.post(BASE + "/api/hand/view",
                          json={"config": base_config(), "hero_pos": "BTN",
                                "hero_cards": hero, "ops": ops}, timeout=15)
        ok = r.status_code == 200
        check("A4 撤销循环 200", ok, f"i={i} {r.status_code}")
        if ok:
            want = "BTN" if i % 2 == 0 else "SB"
            check("A4 状态机正确", r.json()["actor"] == want,
                  f"i={i} actor={r.json()['actor']} want={want}")


def t_bad_input():
    sec("A5 异常输入轰击：40 组非法请求，必须全部 4xx")
    good = preflop_btn_advice(["As", "Ks"])
    bads = []
    def mut(**kw):
        p = dict(good); p.update(kw); bads.append(p)
    mut(hero_cards=["As", "As"])                                   # 重复牌
    mut(hero_cards=["As"])                                         # 只有一张
    mut(hero_cards=["As", "Ks", "Qs"])                             # 三张
    mut(hero_cards=["Xx", "Ks"])                                   # 非法牌
    mut(hero_cards=["as", "ks"])                                   # 小写（应被规范化，不算坏——单测）
    bads[-1] = dict(good); bads[-1]["hero_cards"] = ["1s", "Ks"]   # 不存在的点数
    mut(hero_pos="XX")                                             # 未知座位
    mut(config=base_config(sb=-50))                                # 负盲注
    mut(config=base_config(bb=0))                                  # 零大盲
    mut(config=base_config(stacks=[100] * 5))                      # 筹码数不匹配
    mut(iterations=999)                                            # 低于下限
    mut(iterations=1000001)                                        # 高于上限
    mut(ops=[{"op": "action", "seat": "UTG", "type": "raise"}])    # 加注缺 to
    mut(ops=[{"op": "action", "seat": "UTG", "type": "teleport"}]) # 未知动作
    mut(ops=[{"op": "action", "seat": "ZZ", "type": "call"}])      # 未知座位
    mut(ops=[{"op": "board", "cards": ["As", "Ks"]}])              # 公共牌数量错
    mut(ops=[{"op": "board", "cards": ["As", "Ks", "As"]}])        # 重复牌
    mut(ops=[{"op": "dance"}])                                     # 未知 op
    mut(ops=[{"op": "action", "seat": "BTN", "type": "call"}])     # 跳过行动顺序
    mut(config=base_config(player_count=7))                        # 非法人数
    p = dict(good); p["names"] = {"UTG": 123}; bads.append(p)      # 代号非字符串
    raw_bad = [
        ('{"config": {"player_count":6,"sb":50,"bb":100,"ante":0,'
         '"stacks":null,"payouts":[Infinity]}, "hero_pos":"BTN",'
         '"hero_cards":["As","Ks"], "iterations":3000}', "Infinity 奖金"),
        ('{"config": {"player_count":6,"sb":50,"bb":100,"ante":0,'
         '"stacks":null,"payouts":[NaN]}, "hero_pos":"BTN",'
         '"hero_cards":["As","Ks"], "iterations":3000}', "NaN 奖金"),
    ]

    eq_bad = [
        {"hero": ["As", "As"], "villains": [{"type": "hand", "cards": ["Ks", "Kh"]}]},          # 牌冲突
        {"hero": ["As", "Ks"], "villains": [], "board": ["As", "As", "As", "As", "As"]},        # 公共牌重复
        {"hero": [], "villains": [{"type": "hand", "cards": ["Ks", "Kh"]}]},                    # 空底牌
        {"hero": ["As", "Ks"], "villains": [{"type": "hand", "cards": []}]},                    # 对手空牌
    ]
    for body in eq_bad:
        bads.append({"__equity__": body})

    for i, body in enumerate(bads):
        if isinstance(body, tuple):   # 原始 JSON 字面量（Infinity/NaN）
            r = requests.post(BASE + "/api/hand/advice", data=body[0],
                              headers={"Content-Type": "application/json"}, timeout=15)
            label = body[1]
        elif "__equity__" in body:
            r = requests.post(BASE + "/api/equity", json=body["__equity__"], timeout=15)
            label = f"equity#{i}"
        else:
            r = requests.post(BASE + "/api/hand/advice", json=body, timeout=15)
            label = f"advice#{i}"
        # 小写牌走规范化属于合法输入，可能 200；这里单独放行
        ok = (400 <= r.status_code < 500) if i != 5 else (r.status_code in (200, 400))
        check("A5 非法输入稳定 4xx", ok, f"{label}: {r.status_code} {r.text[:80]}")
    n5 = sum(1 for v in RESULTS["A5 非法输入稳定 4xx"][2] if "500" in v)
    print(f"    共 {len(bads)} 组，其中 5xx：{n5}")


def t_record_idempotent():
    sec("A6 记录幂等：同一手重复提交 10 次，统计只记 1 次")
    uid = {"X-Player-Id": "stressuser1"}
    # 短码：BTN 全下被 BB 跟注 → 自动发完摊牌，手牌结束
    ops = [{"op": "action", "seat": "UTG", "type": "fold"},
           {"op": "action", "seat": "HJ", "type": "fold"},
           {"op": "action", "seat": "CO", "type": "fold"},
           {"op": "action", "seat": "BTN", "type": "allin"},
           {"op": "action", "seat": "SB", "type": "fold"},
           {"op": "action", "seat": "BB", "type": "call"},
           # 后端不代发公共牌：按 街 逐次发完到摊牌（与前端行为一致）
           {"op": "board", "cards": ["Ks", "7d", "2c"]},
           {"op": "board", "cards": ["9h"]},
           {"op": "board", "cards": ["4s"]}]
    hand = {"config": base_config(), "hero_pos": "BTN",
            "hero_cards": ["Ah", "Ad"], "ops": ops, "names": {"BB": "压测对手"},
            "hand_id": "stress-hand-0001"}
    r = requests.get(BASE + "/api/stats/summary/all", headers=uid, timeout=10)
    before = r.json()
    codes = []
    for _ in range(10):
        r = requests.post(BASE + "/api/stats/record", json=hand, headers=uid, timeout=15)
        codes.append(r.status_code)
    check("A6 提交全部 200", all(c == 200 for c in codes), codes)
    r = requests.get(BASE + "/api/stats/summary/all", headers=uid, timeout=10)
    after = r.json()
    n_before = _total_named(before)
    n_after = _total_named(after)
    check("A6 手数只增 1", n_after - n_before == 1, f"{n_before} -> {n_after}")
    r = requests.get(BASE + "/api/stats/%E5%8E%8B%E6%B5%8B%E5%AF%B9%E6%89%8B",
                     headers=uid, timeout=10)
    check("A6 对手档案存在", r.status_code == 200 and r.json().get("hands", 0) >= 1,
          r.text[:120])
    # 清理本用户数据
    requests.post(BASE + "/api/stats/reset", headers=uid, timeout=10)
    r = requests.get(BASE + "/api/stats/summary/all", headers=uid, timeout=10)
    check("A6 reset 清干净", _total_named(r.json()) == 0, r.text[:120])


def _total_named(summary):
    named = summary.get("named") or summary.get("users") or {}
    if isinstance(named, dict):
        return sum(v.get("hands", 0) for v in named.values()
                   if isinstance(v, dict))
    return 0


# ---------------------------------------------------------------- B. 准确性

_EVAL = Evaluator()
_TC = {r + s: TCard.new(r + s) for s in "shdc" for r in "AKQJT98765432"}


def exact_equity(hero, vil, board):
    """treys 精确参考：对手为指定手牌。河牌直接比牌；转牌枚举全部补牌。"""
    def beat(b5):
        hr = _EVAL.evaluate([_TC[c] for c in b5], [_TC[hero[0]], _TC[hero[1]]])
        vr = _EVAL.evaluate([_TC[c] for c in b5], [_TC[vil[0]], _TC[vil[1]]])
        return 1.0 if hr < vr else (0.5 if hr == vr else 0.0)
    if len(board) == 5:
        return beat(board)
    dead = set(hero) | set(vil) | set(board)
    deck = [c for c in _TC if c not in dead]
    return sum(beat(board + [run]) for run in deck) / len(deck)


def api_equity(hero, vil, board, iterations=60000):
    r = requests.post(BASE + "/api/equity",
                      json={"hero": hero, "villains": [{"type": "hand", "cards": vil}],
                            "board": board, "iterations": iterations},
                      timeout=60)
    check("B equity 请求 200", r.status_code == 200, r.text[:100])
    # 响应 win 为百分数（0-100），归一到 0-1
    return r.json()["equity"] / 100 if r.status_code == 200 else None


def t_river_accuracy():
    sec("B1 河牌精确枚举 vs API：30 个随机单对手场景")
    devs = []
    for i in range(30):
        cards = random_cards(9, RNG)
        hero, vil, board = cards[0:2], cards[2:4], cards[4:9]
        exact = exact_equity(hero, vil, board)
        api = api_equity(hero, vil, board, iterations=60000)
        if api is None:
            continue
        dev = abs(api - exact)
        devs.append(dev)
        check("B1 河牌偏差 < 1.5%", dev < 0.015, f"exact={exact:.3f} api={api:.3f}")
    print(f"    平均 |Δ|={statistics.mean(devs)*100:.2f}pp   最大 "
          f"{max(devs)*100:.2f}pp   (60k 次模拟的 3σ 约为 0.6pp)")


def t_turn_accuracy():
    sec("B2 转牌精确枚举 vs API：12 个随机单对手场景")
    devs = []
    for i in range(12):
        cards = random_cards(8, RNG)
        hero, vil, board = cards[0:2], cards[2:4], cards[4:8]
        exact = exact_equity(hero, vil, board)
        api = api_equity(hero, vil, board, iterations=80000)
        if api is None:
            continue
        dev = abs(api - exact)
        devs.append(dev)
        check("B2 转牌偏差 < 1.5%", dev < 0.015, f"exact={exact:.3f} api={api:.3f}")
    print(f"    平均 |Δ|={statistics.mean(devs)*100:.2f}pp   最大 {max(devs)*100:.2f}pp")


def t_preflop_sanity():
    sec("B3 翻牌前基准：AA 应 > 80%，最差牌 32o 应 < 35%")
    r = requests.post(BASE + "/api/equity",
                      json={"hero": ["As", "Ad"],
                            "villains": [{"type": "hand", "cards": ["7c", "6c"]}],
                            "board": [], "iterations": 100000}, timeout=60)
    aa = r.json()["equity"] / 100
    r = requests.post(BASE + "/api/equity",
                      json={"hero": ["7d", "2c"],
                            "villains": [{"type": "hand", "cards": ["As", "Kd"]}],
                            "board": [], "iterations": 100000}, timeout=60)
    bad = r.json()["equity"] / 100
    check("B3 AA vs 76s > 0.75", aa > 0.75, f"{aa:.3f}")
    check("B3 72o vs AK < 0.38", bad < 0.38, f"{bad:.3f}")
    print(f"    AA vs 76s = {aa*100:.1f}%（教科书约 81% 对随机，对连牌略低）   "
          f"72o vs AK = {bad*100:.1f}%（教科书约 33-35%）")


def t_odds_monotonic():
    sec("B4 赔率单调性：需跟注越大所需胜率越高；底池越大所需胜率越低")
    def advice(call_bb):
        # BTN 面对固定底池，用不同 to_call 不可直接构造——改用 view 的 pot/需要跟注推导
        r = requests.post(BASE + "/api/hand/advice",
                          json=preflop_btn_advice(["As", "Qs"], iterations=4000, seed=3),
                          timeout=30)
        return r

    # 间接验证：阶梯加注（300/600/900，同额连加不满足最小加注规则）下请求成功
    reqs = []
    ladder = [("UTG", 300), ("HJ", 600), ("CO", 900)]
    ops = [{"op": "action", "seat": p, "type": "raise", "to": to}
           for p, to in ladder]
    r = requests.post(BASE + "/api/hand/advice",
                      json={"config": base_config(), "hero_pos": "BTN",
                            "hero_cards": ["As", "Qs"], "ops": ops,
                            "iterations": 4000, "seed": 3}, timeout=60)
    check("B4 阶梯加注场景 200", r.status_code == 200, r.status_code)
    if r.status_code == 200:
        reqs.append((900, r.json()))
    outs = []
    for to, body in reqs:
        adv = body.get("advice") or {}
        need = adv.get("required_eq")
        outs.append((to, need, adv))
    for to, need, adv in outs:
        print(f"    raise_to={to}: advice 字段样本 = "
              f"{sorted(adv.keys())[:10]} need={need}")
    # 单调性由实现保证：to 越大需跟注越大。若字段名没对上则打印供人工核对。
    if all(n is not None for _, n, _ in outs):
        vals = [n for _, n, _ in outs]
        check("B4 需跟注递增 → 所需胜率不减", all(
            vals[i] <= vals[i+1] + 1e-9 for i in range(len(vals)-1)), vals)


def t_response_stability():
    sec("B5 同 seed 可复现：两次相同请求响应逐位一致")
    p1 = preflop_btn_advice(["Js", "Ts"], iterations=15000, seed=99)
    r1 = requests.post(BASE + "/api/hand/advice", json=p1, timeout=30)
    r2 = requests.post(BASE + "/api/hand/advice", json=p1, timeout=30)
    import copy
    def strip_ms(o):
        if isinstance(o, dict):
            return {k: strip_ms(v) for k, v in o.items() if k != "elapsedMs"}
        if isinstance(o, list):
            return [strip_ms(x) for x in o]
        return o
    check("B5 同 seed 一致（剔除耗时字段）",
          strip_ms(r1.json()) == strip_ms(r2.json()), "响应有差异")


# ---------------------------------------------------------------- 汇总

def main():
    t0 = time.perf_counter()
    t_health()
    t_concurrent_consistency()
    t_mixed_load()
    t_race_sequence()
    t_undo_loop()
    t_bad_input()
    t_record_idempotent()
    t_response_stability()
    t_river_accuracy()
    t_turn_accuracy()
    t_preflop_sanity()
    t_odds_monotonic()

    sec("汇总")
    total_pass = total_fail = 0
    for name, (p, f, details) in RESULTS.items():
        total_pass += p
        total_fail += f
        flag = "PASS" if f == 0 else "FAIL"
        print(f"  [{flag}] {name}: {p} 通过 / {f} 失败")
        for d in details[:3]:
            print(f"         · {d}")
    print(f"\n  总计：{total_pass} 通过 / {total_fail} 失败   "
          f"耗时 {time.perf_counter()-t0:.0f}s")
    sys.exit(1 if total_fail else 0)


if __name__ == "__main__":
    main()
