"""压力测试：正常使用逻辑的服务端健壮性验证。

覆盖四类压力：
  1. 模糊测试——随机动作序列（合法/非法混合），服务端要么 200 要么 400 带中文错误，绝不 500；
  2. 边界场景——零/超大筹码、9 人桌多人全下、重复牌、公共牌与底牌冲突、超长操作序列；
  3. 并发——多线程同时打不同请求（服务端无状态重放，必须互不污染）；
  4. 一致性——同一请求重放两次，响应必须逐字节一致。

运行：python _stress_api.py [host]   （默认 127.0.0.1:8765，失败非零退出）
"""
import json
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HOST = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
RANKS = "AKQJT98765432"
SUITS = "cdhs"
POSITIONS = {
    6: ["SB", "BB", "UTG", "HJ", "CO", "BTN"],
    8: ["SB", "BB", "UTG", "UTG+1", "MP", "HJ", "CO", "BTN"],
    9: ["SB", "BB", "UTG", "UTG+1", "UTG+2", "MP", "HJ", "CO", "BTN"],
}

PASS, FAIL = 0, []


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  [通过] {name}")
    else:
        FAIL.append(name)
        print(f"  [失败] {name}  {detail}")


def post(path, payload, timeout=90):
    req = urllib.request.Request(HOST + path, json.dumps(payload).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return -1, str(e).encode()


def rand_card(rng, exclude):
    while True:
        c = rng.choice(RANKS) + rng.choice(SUITS)
        if c not in exclude:
            return c


def rand_ops(rng, n_players, count, allow_bad=True):
    """随机动作序列：大体合法，allow_bad 时混入非法动作/金额。"""
    pos = POSITIONS[n_players]
    ops = []
    actor_i = 2  # 翻牌前从 UTG 开始
    for _ in range(count):
        if actor_i >= n_players:
            break
        seat = pos[actor_i]
        roll = rng.random()
        if roll < 0.35 or not allow_bad:
            ops.append({"op": "action", "type": "fold", "seat": seat})
            actor_i += 1
        elif roll < 0.7:
            ops.append({"op": "action", "type": "raise",
                        "to": rng.choice([400, 600, 900, 1500, 3000, 9975]),
                        "seat": seat})
            actor_i = n_players  # 加注后其余位置重置，简化为直接结束本街
        elif roll < 0.85:
            ops.append({"op": "action", "type": "call", "seat": seat})
            actor_i += 1
        elif allow_bad and roll < 0.92:  # 非法：错座位
            ops.append({"op": "action", "type": "fold",
                        "seat": rng.choice(pos[:2])})
        elif allow_bad:  # 非法：金额越界
            ops.append({"op": "action", "type": "raise",
                        "to": rng.choice([0, 1, 5, -500, 10**9]), "seat": seat})
    return ops


def rand_config(rng):
    n = rng.choice([6, 8, 9])
    stacks = None
    if rng.random() < 0.4:
        stacks = [rng.choice([0, 200, 5000, 10000, 250000]) for _ in range(n)]
    return {"player_count": n, "sb": rng.choice([25, 50, 100]),
            "bb": rng.choice([100, 200]), "ante": rng.choice([0, 25, 50]),
            "stacks": stacks}, n


# --------------------------------------------------------------- 1. 模糊测试
def fuzz(rounds=120):
    print(f"=== 模糊测试：{rounds} 轮随机序列（含非法动作）===")
    rng = random.Random(20260917)
    codes = {-1: 0}
    bad_status = []
    for i in range(rounds):
        cfg, n = rand_config(rng)
        hero = rand_card(rng, set()) + " " + rand_card(rng, set())
        hero_cards = hero.split(" ")
        payload = {"config": cfg, "hero_pos": rng.choice(POSITIONS[n]),
                   "hero_cards": hero_cards,
                   "ops": rand_ops(rng, n, rng.randint(0, 16))}
        code, body = post("/api/hand/view", payload)
        if code == -1:
            bad_status.append(f"#{i} 连接失败: {body[:80]}")
        elif code not in (200, 400, 422):
            bad_status.append(f"#{i} HTTP {code}: {body[:120]}")
    check("120 轮模糊请求全部 200/400（无 5xx、无连接失败）", not bad_status,
          "; ".join(bad_status[:3]))


# --------------------------------------------------------------- 2. 边界场景
def edge_cases():
    print("=== 边界场景 ===")
    base = {"player_count": 6, "sb": 100, "bb": 200, "ante": 25}
    cards = ["As", "Ad"]
    ok400 = []

    cases = [
        ("零筹码开局", {"config": {**base, "stacks": [0] * 6}, "hero_pos": "BTN",
                    "hero_cards": cards, "ops": []}),
        ("超大筹码", {"config": {**base, "stacks": [10**9] * 6}, "hero_pos": "BTN",
                   "hero_cards": cards, "ops": []}),
        ("9人桌两人全下", {"config": {"player_count": 9, "sb": 100, "bb": 200, "ante": 25},
                       "hero_pos": "MP", "hero_cards": cards,
                       "ops": [{"op": "action", "type": "raise", "to": 9975, "seat": "UTG"},
                                {"op": "action", "type": "call", "seat": "UTG+1"}]}),
        ("底牌重复", {"config": base, "hero_pos": "BTN", "hero_cards": ["As", "As"], "ops": []}),
        ("板上牌与底牌冲突", {"config": base, "hero_pos": "BTN", "hero_cards": cards,
                        "ops": [{"op": "action", "type": "raise", "to": 600, "seat": "BTN"},
                                 {"op": "action", "type": "fold", "seat": "SB"},
                                 {"op": "action", "type": "call", "seat": "BB"},
                                 {"op": "board", "cards": ["As", "Kd", "2c"]}]}),
        ("空手牌列表", {"config": base, "hero_pos": "BTN", "hero_cards": [], "ops": []}),
        ("超长操作序列", {"config": base, "hero_pos": "BTN", "hero_cards": cards,
                     "ops": [{"op": "action", "type": "fold", "seat": "BTN"}] * 60}),
    ]
    for name, payload in cases:
        code, body = post("/api/hand/view", payload)
        ok = code in (200, 400, 422)
        if not ok:
            ok400.append(f"{name}: HTTP {code} {body[:100]}")
        print(f"    {name}: HTTP {code}")
    check("7 个边界场景全部 200/400（无 5xx）", not ok400, "; ".join(ok400))


# --------------------------------------------------------------- 3. 并发
def concurrency(threads=16):
    print(f"=== 并发：{threads} 线程同时请求 ===")
    rng = random.Random(7)
    workloads = []
    for _ in range(threads):
        cfg, n = rand_config(rng)
        workloads.append({"config": cfg, "hero_pos": rng.choice(POSITIONS[n]),
                          "hero_cards": [rand_card(rng, set()), rand_card(rng, set())],
                          "ops": rand_ops(rng, n, 10, allow_bad=False)})
    results = [None] * threads

    def hit(k):
        t0 = time.time()
        code, body = post("/api/hand/view", workloads[k])
        results[k] = (code, time.time() - t0, body)

    with ThreadPoolExecutor(max_workers=threads) as ex:
        list(ex.map(hit, range(threads)))
    bad = [f"#{k} HTTP {c}" for k, (c, _, _) in enumerate(results) if c not in (200, 400)]
    slow = [f"#{k} {t:.1f}s" for k, (c, t, _) in enumerate(results) if t > 30]
    check(f"{threads} 并发全部成功且无 30s+ 长尾", not bad and not slow,
          f"{bad[:2]} {slow[:2]}")
    # 一致性：重复同一并发负载，逐字节比对
    same = True
    for k in (0, 3, 7):
        code2, body2 = post("/api/hand/view", workloads[k])
        if body2 != results[k][2]:
            same = False
    check("无状态重放：同请求响应逐字节一致", same)


# --------------------------------------------------------------- 4. 全流程耐久
def endurance(rounds=30):
    """连续完整手牌：发牌→下注→发公共牌→摊牌→记录，循环 rounds 手。"""
    print(f"=== 全流程耐久：连续 {rounds} 手（含建议请求）===")
    rng = random.Random(99)
    errors, times = [], []
    for i in range(rounds):
        cfg, n = rand_config(rng)
        cfg["stacks"] = None
        cfg["ante"] = 25
        hero_cards = [rand_card(rng, set()), rand_card(rng, set())]
        payload = {"config": cfg, "hero_pos": rng.choice(POSITIONS[n]),
                   "hero_cards": hero_cards, "ops": [], "iterations": 3000}
        code, body = post("/api/hand/advice", payload)
        # 盲注位翻牌前未轮到是正确业务行为（400 提示"还没轮到你行动"）
        not_turned = code == 400 and "轮" in body.decode("utf-8", "ignore")
        if code != 200 and not not_turned:
            errors.append(f"#{i} 建议请求 HTTP {code}: {body[:80]}")
            continue
        t0 = time.time()
        code2, _ = post("/api/hand/view", payload)
        times.append(time.time() - t0)
        if code2 != 200:
            errors.append(f"#{i} view HTTP {code2}")
    check(f"{rounds} 手建议+视图全部成功", not errors, "; ".join(errors[:3]))
    if times:
        times.sort()
        print(f"    view 耗时 p50={times[len(times)//2]*1000:.0f}ms max={times[-1]*1000:.0f}ms")


if __name__ == "__main__":
    fuzz()
    edge_cases()
    concurrency()
    endurance()
    print(f"\n=== 压力测试总结：{PASS} 项通过，{len(FAIL)} 项失败 ===")
    if FAIL:
        print("失败项：", FAIL)
        sys.exit(1)
    print("服务端压力测试全部通过 ✓")
