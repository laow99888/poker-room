# -*- coding: utf-8 -*-
"""建议模块模糊测试：随机生成大量合法局面，校验建议不变量与策略合理性。"""
import warnings, random, sys
warnings.filterwarnings("ignore")
sys.path.insert(0, r"C:/Users/Administrator/.zcode/workspace/default/poker-tools")

from backend.table import build_state, deal_hole, apply_ops, positions_for
from backend.decision import advice_for

ALL_CARDS = [r + s for s in "cdhs" for r in "AKQJT98765432"]
rng = random.Random(20260917)

bugs = []          # 硬性不变量违反
suspect = []       # 策略可疑（调优信号）
checked = 0

def note_bug(kind, detail):
    bugs.append(f"{kind}: {detail}")

def gen_scenario(rng):
    n = rng.choice([6, 8, 9])
    sb, bb = rng.choice([(50, 100), (100, 200), (25, 50), (200, 400)])
    ante = rng.choice([0, 0, sb // 2, sb])
    positions = positions_for(n)
    hero_pos = rng.choice(positions)
    hero_i = positions.index(hero_pos)
    deck = ALL_CARDS[:]
    rng.shuffle(deck)
    hero = [deck.pop(), deck.pop()]
    stacks = [max(bb, rng.choice([bb * rng.randint(2, 6), bb * rng.randint(10, 60)])) for _ in range(n)]
    cfg = {"player_count": n, "sb": sb, "bb": bb, "ante": ante, "stacks": stacks}
    return cfg, positions, hero_i, hero, rng

for scenario in range(300):
    cfg, positions, hero_i, hero, rng = gen_scenario(rng)
    try:
        state = build_state(cfg)
        deal_hole(state, hero_i, hero)
    except Exception as exc:
        continue
    # 随机推进若干步
    for _step in range(rng.randint(1, 14)):
        try:
            if state.status is False:
                break
            if state.actor_index is None:
                need = state.streets[state.street_index].board_dealing_count
                if need == 0:
                    break
                dealable = [repr(c) for c in state.get_dealable_cards()]
                cards = rng.sample(dealable, need)
                apply_ops(state, [{"op": "board", "cards": cards}])
                continue
            if state.actor_index == hero_i:
                # 到达英雄决策点：跑建议并校验
                try:
                    adv = advice_for(state, hero_i, iterations=3000, seed=rng.randint(1, 10**6))
                except Exception as exc:
                    note_bug("advice异常", f"{type(exc).__name__}: {exc}")
                    break
                checked += 1
                rec = adv["recommendation"]
                eq = adv["equity"]
                # 不变量 1：权益和
                if abs(eq["win"] + eq["tie"] + eq["lose"] - 100) > 0.02:
                    note_bug("权益和≠100", str(eq))
                # 不变量 2：required_eq 复算
                exp_req = round(adv["to_call"] * 100 / (adv["pot"] + adv["to_call"]), 2) if adv["to_call"] > 0 else 0.0
                if adv["required_eq"] != exp_req:
                    note_bug("required_eq 错", f"{adv['required_eq']} != {exp_req}")
                # 不变量 3：to_call=0 时不建议弃牌
                if adv["to_call"] == 0 and any(m["action"] == "fold" for m in rec["mix"]):
                    note_bug("无注却建议弃牌", str(rec["mix"]))
                # 不变量 4：mix 概率
                pcts = [m["pct"] for m in rec["mix"]]
                if not all(0 <= p <= 100 for p in pcts):
                    note_bug("概率越界", str(pcts))
                if abs(sum(pcts) - 100) > 2:
                    note_bug("概率和≠100", str(pcts))
                if pcts != sorted(pcts, reverse=True):
                    note_bug("mix 未按概率降序", str(rec["mix"]))
                # 不变量 5：primary = 概率最高
                if rec["primary"] != rec["mix"][0]["label"]:
                    note_bug("primary 与首项不符", rec["primary"])
                # 不变量 6：SPR 复算
                if rec["spr"] is not None:
                    exp_spr = round(adv["effective_stack"] / adv["pot"], 1) if adv["pot"] > 0 else None
                    if rec["spr"] != exp_spr:
                        note_bug("SPR 错", f"{rec['spr']} != {exp_spr}")
                # 策略合理性信号
                eff = eq["win"] + eq["tie"] / 2
                fold_top = rec["mix"][0]["action"] == "fold"
                call_top = rec["mix"][0]["action"] in ("call", "check")
                if adv["to_call"] > 0 and fold_top and eff > adv["required_eq"] + 20:
                    suspect.append(f"过紧: eff={eff} req={adv['required_eq']} mix={rec['mix']}")
                if adv["to_call"] > 0 and call_top and eff < adv["required_eq"] - 10:
                    suspect.append(f"过松: eff={eff} req={adv['required_eq']} mix={rec['mix']}")
                break  # 本决策点只校验一次，然后随机继续推进
            # 非英雄：随机行动
            r = rng.random()
            try:
                if r < 0.3:
                    state.fold()
                elif r < 0.75:
                    state.check_or_call()
                elif state.max_completion_betting_or_raising_to_amount is not None:
                    hi = state.max_completion_betting_or_raising_to_amount
                    lo = state.min_completion_betting_or_raising_to_amount
                    to = rng.randint(lo, hi)
                    state.complete_bet_or_raise_to(to)
                else:
                    state.check_or_call()
            except ValueError:
                break
        except ValueError:
            break

print(f"共校验决策点: {checked}")
print(f"硬性 bug: {len(bugs)}")
for b in bugs[:15]:
    print("  ✗", b)
print(f"策略可疑信号: {len(suspect)}")
for s_ in suspect[:8]:
    print("  ?", s_)
