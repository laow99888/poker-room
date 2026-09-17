# -*- coding: utf-8 -*-
"""全清单审计脚本：41 条审查问题逐项取证（原始 19 + 复核 20-32 + 二轮 33-41）。

用途：任何一轮修复后运行 `python -X utf8 _verify_all.py`，直接对全部已知
问题重放原始复现场景/核对源码落点，输出逐项 PASS/FAIL 全表。
运行一次约 2-3 分钟；与 tests/ 的单元/契约测试互补，不替代 pytest。
"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import os, subprocess, tempfile, pathlib
from backend import opponents
from fastapi.testclient import TestClient
from backend.app import app

results = []
def check(no, desc, cond):
    results.append((str(no), desc, bool(cond)))

c = TestClient(app)
tmp = tempfile.mkdtemp()
opponents._DATA = pathlib.Path(tmp) / "opp.json"
opponents._DATA.write_text("{}", encoding="utf-8")

BOARD5 = ["2c", "3d", "7h", "9c", "Td"]

# ══════════ 原始 19 条 ══════════
from backend.equity import simulate
from backend.ranges import expand_range as er
from backend.cfr import solve_river
from backend.table import replay_state, replay, hand_view
from backend.decision import player_intel, _chen_score
from backend.textures import range_equity

# 01 范围采样：冲突报错 + 顺序无关
try:
    simulate(["As", "Ah"], [{"type": "range", "text": "AsAh"}], BOARD5, 500, seed=1)
    ok01 = False
except Exception:
    ok01 = True
a = simulate(["As", "Ah"], [{"type": "hand", "cards": ["Ks", "Kh"]}], BOARD5, 2000, seed=42)
b = simulate(["As", "Ah"], [{"type": "hand", "cards": ["Kh", "Ks"]}], BOARD5, 2000, seed=42)
a.pop("elapsedMs"); b.pop("elapsedMs")
check("01", "冲突范围明确报错 + 组合顺序不影响结果", ok01 and a == b)

# 02 混合桶权益域
r02 = solve_river(BOARD5, [("Ks", "Kh")], er("AA") + er("QQ")[:4], pot_bb=2,
                  to_call_bb=1, stack_bb=1.5, buckets=1, iterations=1600)
check("02", "一桶混合 40% 权益 call 为主（非必败）",
      r02["hero_strength"][0] == 0.4 and r02["avg_strategy"][(0, 0, "")]["call"] > 0.9)

# 03 金额口径：不足额封顶 + money/旧参数一致性
r03 = solve_river(BOARD5, [("Ks", "Kh")], er("AA") + er("QQ")[:4],
                  money=(1, 10, 0, 2, 0), buckets=1, iterations=1600)
kw = dict(pot_bb=2, to_call_bb=1, stack_bb=1.5, bet_sizes=(1.0,), buckets=4, iterations=300)
check("03", "不足额跟注 call + 默认派生与 money 一致",
      r03["avg_strategy"][(0, 0, "")]["call"] > 0.9
      and solve_river(BOARD5, [("Ks", "Kh")], er("AA") + er("QQ")[:4], **kw)["avg_strategy"]
      == solve_river(BOARD5, [("Ks", "Kh")], er("AA") + er("QQ")[:4],
                     money=(0.5, 1.5, 0.0, 1.0, 0.0),
                     bet_sizes=(1.0,), buckets=4, iterations=300)["avg_strategy"])

# 04 record 正常入库
cfg6 = {"sb": 100, "bb": 200, "ante": 0, "player_count": 6}
ops_fold = [{"op": "action", "type": "fold", "seat": "UTG"},
            {"op": "action", "type": "fold", "seat": "HJ"},
            {"op": "action", "type": "fold", "seat": "CO"},
            {"op": "action", "type": "fold", "seat": "BTN"},
            {"op": "action", "type": "fold", "seat": "SB"}]
s4, h4, _ = replay_state(cfg6, "BB", ["As", "Ad"], ops_fold)
intel4 = player_intel(s4)
r4 = opponents.record_hand(cfg6, ops_fold, {"BTN": "测试员"}, intel4, hero_index=1, uid="v04")
check("04", "record 正常入库（不再 500）", r4["recorded"] is True)

# 05 占位牌避开声明公共牌 + simulated + taken
ops05 = [{"op": "action", "type": "fold", "seat": "UTG"},
         {"op": "action", "type": "fold", "seat": "HJ"},
         {"op": "action", "type": "fold", "seat": "CO"},
         {"op": "action", "type": "call", "seat": "BTN"},
         {"op": "action", "type": "fold", "seat": "SB"},
         {"op": "action", "type": "check", "seat": "BB"}]
v5c = replay(cfg6, "BTN", ["As", "Ad"],
             ops05 + [{"op": "board", "cards": ["Kh", "Qd", "8c"]}])
check("05", "声明公共牌成功 + simulated 标记 + taken 仅已知牌",
      v5c["board"] == ["Kh", "Qd", "8c"] and v5c["simulated"] is True
      and v5c["taken"] == sorted(["As", "Ad", "8c", "Kh", "Qd"]))

# 06 多家平分份额 + 边池 EV 测试存在
eq6 = simulate(["As", "Ah"], [{"type": "hand", "cards": ["Ks", "Kh"]},
                              {"type": "hand", "cards": ["Qs", "Qh"]}],
               ["5c", "6d", "7h", "8s", "9c"], 3000, seed=7)
t_api = open("tests/test_decision_api.py", encoding="utf-8").read()
check("06", "三家平分 share≈33.3%（非 50%）+ 边池 EV 测试在库",
      abs(eq6["equity"] - 33.33) < 0.5
      and "test_side_pot_ev_drives_recommendation" in t_api)

# 07 翻前分类门控
ops07 = ops05 + [{"op": "board", "cards": ["2c", "3d", "7h"]},
                 {"op": "action", "type": "raise", "to": 600, "seat": "BB"}]
s7, h7, _ = replay_state(cfg6, "BTN", ["As", "Ad"], ops07)
check("07", "BB 翻前 check 不被翻后加注污染", player_intel(s7)[1]["line"] == "check")

# 08 chen 点数
check("08", "22=5 < 99=9 < TT=10 < AA=20",
      _chen_score((("2", "s"), ("2", "h"))) == 5
      and _chen_score((("9", "s"), ("9", "h"))) == 9
      and _chen_score((("T", "s"), ("T", "h"))) == 10
      and _chen_score((("A", "s"), ("A", "h"))) == 20)

# 09/10/13/26/28 前端纪律（源码落点）
appjs = open("frontend/app.js", encoding="utf-8").read()
guard = open("tests/frontend/async-guard.test.mjs", encoding="utf-8").read()
check("09", "sameHand 契约 + revision 守卫（测试与源码在位）",
      "typeof before === \"string\"" in open("frontend/logic.js", encoding="utf-8").read()
      and "36-A" in guard)
check("10", "仅回滚待确认一步（无连环 pop）",
      "retries < 3" not in appjs and "state.unconfirmed" in appjs)
check("13", "resetHandState 统一迁移 + 推进 revision",
      "function resetHandState" in appjs and "state.revision += 1" in appjs)
check("26", "错误优先渲染 + retry 按钮",
      'if (state.error) {' in appjs and 'data-action="retry"' in appjs)
check("28", "清底牌统一 resetHandState 并保留另一张",
      'case "hero-pick":' in appjs and "kept" in appjs)

# 11/38 reset 令牌门槛
check("11", "reset 令牌门槛保留", "X-Admin-Token" in open("backend/app.py", encoding="utf-8").read())

# 12 ICM 手前快照
v12 = c.post("/api/hand/view", json={"config": {"sb": 100, "bb": 200, "player_count": 6,
             "payouts": [600]}, "hero_pos": "UTG", "hero_cards": ["As", "Ad"], "ops": []}).json()
check("12", "ICM 手前快照 10000×6", v12["icm"].get("snapshot") == "hand-start"
      and {r["stack"] for r in v12["icm"]["rows"]} == {10000})

# 14 hand_id 幂等
opponents.record_hand(cfg6, ops_fold, {}, intel4, hand_id="first", uid="v14")
for i in range(810):
    opponents.record_hand(cfg6, ops_fold, {}, intel4, hand_id=f"x{i}", uid="v14")
r14 = opponents.record_hand(cfg6, ops_fold, {}, intel4, hand_id="first", uid="v14")
r14b = opponents.record_hand(cfg6, ops_fold, {}, intel4, hand_id="new-id", uid="v14")
check("14", "810 手后同 id 仍判重、新 id 各计", r14["recorded"] is False
      and r14b["recorded"] is True)

# 15 转义
check("15", "名字输出转义（输入框+建议面板）",
      "AppLogic.escapeHtml(opponentName(p))" in appjs
      and "AppLogic.escapeHtml(o.name)" in appjs)

# 16 非法输入 4xx
bad1 = c.post("/api/equity", json={"hero": ["As"], "villains": [], "board": []})
bad2 = c.post("/api/hand/view", json={"config": {"sb": 100, "bb": 200},
              "hero_pos": "BTN", "hero_cards": ["As", "Ad"],
              "ops": [{"op": "action", "type": "raise", "to": "abc", "seat": "BTN"}]})
bad3 = c.post("/api/hand/view", json={"config": {"sb": 100, "bb": 200,
              "payouts": ["Infinity", 300]}, "hero_pos": "BTN",
              "hero_cards": ["As", "Ad"], "ops": []})
check("16", "非法输入稳定 4xx（三组）",
      bad1.status_code == 400 and bad2.status_code == 400 and bad3.status_code == 422)

# 17/32 范围交集
check("17", "双方同组合范围对不可评估",
      range_equity([("As", "Ad")], [("As", "Ad")], BOARD5, 100, 7) is None)

# 18 seed 一致
a18 = simulate(["As", "Ah"], [{"type": "range", "text": "KK,QQ"}], [], 800, seed=42)
b18 = simulate(["As", "Ah"], [{"type": "range", "text": "kk,QQ"}], [], 800, seed=42)
a18.pop("elapsedMs"); b18.pop("elapsedMs")
check("18", "同 seed 跨大小写输出一致（不含耗时字段）", a18 == b18)

# 19 校验脚本退出码
p19 = subprocess.run(["python", "-X", "utf8", "_verify_layer1.py"], capture_output=True)
check("19", "layer1 校验 exit=0", p19.returncode == 0)

# ══════════ 复核 20-32 回归点 ══════════
base_eq = {"hero": ["As", "Ah"], "villains": [{"type": "range", "text": "KK"}],
           "board": BOARD5, "iterations": 2000, "seed": 42}
e1 = c.post("/api/equity", json=base_eq).json()
base_eq["villains"][0]["text"] = "kk"
e2 = c.post("/api/equity", json=base_eq).json()
e1.pop("elapsedMs"); e2.pop("elapsedMs")
check("20", "kk/KK 等价", e1 == e2)

r21 = solve_river(BOARD5, [("Ks", "Kh")], er("AA") + er("QQ")[:4],
                  money=(1, 49, 0, 47, 0), buckets=1, iterations=100, allow_raise=False)
check("21", "无加注权无 raise", "raise" not in r21["root_labels"])

check("22", "跨手守卫行为测试在库", "40-C" in guard and "36-A" in guard)
check("23", "烧牌不禁选（taken 仅已知观察）",
      v5c["taken"] == sorted(["As", "Ad", "8c", "Kh", "Qd"]))

adv34 = c.post("/api/hand/advice", json={"config": {"sb": 100, "bb": 200, "ante": 0,
               "player_count": 6, "stacks": [10000, 10000, 10000, 10000, 1000, 10000]},
               "hero_pos": "CO", "hero_cards": ["Ks", "Kh"],
               "ops": [{"op": "action", "type": "allin", "seat": "UTG"},
                       {"op": "action", "type": "call", "seat": "HJ"}],
               "iterations": 20000}).json()["advice"]
check("24", "短码可争层 3300 / 30.30%", adv34["required_eq"] == 30.3
      and sum(l["amount"] for l in adv34["side_pots"]) == 3300)

check("25", "revision 等长分支守卫（行为级）", "36-A" in guard)

# 27/37 身份链路
check("27", "advice 携带 X-Player-Id（画像参与建议）",
      '"Content-Type": "application/json", ...statsHeaders()' in appjs)

# 29 首手 id
check("29", "首手即有 handId", "handId: AppLogic.newHandId()" in appjs)

# 30 建议面板转义
check("30", "renderAdvice 名字转义", "escapeHtml(o.name)" in appjs)

# 31 保留前缀降级
opponents.record_hand(cfg6, ops_fold, {"BB": "_pool"}, intel4, uid="v31")
check("31", "保留前缀名降级为无名", "_pool" not in
      opponents._load()["users"]["v31"]["named"])

check("32", "冲突范围对 None",
      range_equity([("As", "Ad")], [("As", "Ad")], BOARD5, 100, 7) is None)

# ══════════ 二轮 33-41 回归点 ══════════
cfg33 = {"sb": 100, "bb": 200, "ante": 0, "player_count": 6,
         "stacks": [10000, 10000, 10000, 10000, 1000, 10000]}
ops33 = [{"op": "action", "type": "fold", "seat": "UTG"},
         {"op": "action", "type": "fold", "seat": "HJ"},
         {"op": "action", "type": "fold", "seat": "CO"},
         {"op": "action", "type": "call", "seat": "BTN"},
         {"op": "action", "type": "fold", "seat": "SB"},
         {"op": "action", "type": "check", "seat": "BB"},
         {"op": "board", "cards": ["2c", "3d", "7h"]},
         {"op": "action", "type": "check", "seat": "BB"},
         {"op": "action", "type": "check", "seat": "BTN"},
         {"op": "board", "cards": ["9c"]},
         {"op": "action", "type": "check", "seat": "BB"},
         {"op": "action", "type": "check", "seat": "BTN"},
         {"op": "board", "cards": ["Td"]},
         {"op": "action", "type": "raise", "to": 200, "seat": "BB"}]
adv33 = c.post("/api/hand/advice", json={"config": cfg33, "hero_pos": "BTN",
               "hero_cards": ["As", "Ad"], "ops": ops33, "iterations": 20000}).json()["advice"]
lo, hi = adv33["min_raise_to"], adv33["max_raise_to"]
ok33 = all(lo - 1e-6 <= float(m["label"].replace("加注到 ", "").replace(" BB", "")) * 200 <= hi + 1e-6
           for m in adv33["cfr"]["mix"] if m["action"] == "raise" and "加注到" in m["label"])
check("33", "CFR 加注本街金额夹在引擎上下限", ok33)

# 35 建议对账（06 已验测试在库）
# 36/40 行为级（22 已验）
# 37 已验（27）

# 38 reset 模式
old_tok = os.environ.pop("POKER_ADMIN_TOKEN", None)
c.post("/api/stats/record", headers={"X-Player-Id": "u-a"}, json={
    "config": cfg6, "hero_pos": "BB", "hero_cards": ["As", "Ad"], "ops": ops_fold,
    "names": {"BB": "老张"}})
r38 = c.post("/api/stats/reset", headers={"X-Player-Id": "u-a"})
ok38 = r38.json() == {"reset": "user"}
if old_tok is not None:
    os.environ["POKER_ADMIN_TOKEN"] = old_tok
check("38", "无令牌 reset 只清自己", ok38)

# 39 迁移不复活
opponents._DATA.write_text('{"alice": {"hands": 7, "vpip": 2, "pfr": 1, "threebet": 0}}',
                           encoding="utf-8")
d39 = opponents._load()
opponents.reset_user("local")
check("39", "迁移消费旧键 + reset 不复活",
      d39.get("schema") == 2 and "alice" not in d39
      and opponents.get_stats("alice", uid="local")["hands"] == 0)

# 41 测试隔离
check("41", "conftest 隔离 + 无恒真断言",
      os.path.exists("tests/conftest.py")
      and "is None or True" not in open("tests/test_opponents.py", encoding="utf-8").read())

fails = [x for x in results if not x[2]]
for no, desc, ok in results:
    print(("  ✓ " if ok else "  ✗ ") + no.rjust(4) + "  " + desc)
print()
print("共 " + str(len(results)) + " 项：" +
      ("全部 PASS" if not fails else str(len(fails)) + " 项 FAIL: " + str([x[0] for x in fails])))
