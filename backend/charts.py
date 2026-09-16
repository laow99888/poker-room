"""位置范围图加载与查询。

默认图在 backend/data/charts.json，可编辑后重启服务生效。
8/9 人桌的新位置（UTG+1、UTG+2、MP、LJ）若某张图缺省，
按 FALLBACK_MAP 回退到相邻位置的同名图。
"""

import json
from functools import lru_cache
from pathlib import Path

from .ranges import expand_range

_DATA = Path(__file__).resolve().parent / "data" / "charts.json"

SITUATION_LABEL = {
    "rfi": "首次加注",
    "three_bet": "再加注（3bet）",
    "call": "跟注加注",
    "defend_SB": "小盲防守",
    "defend_BB": "大盲防守",
    "facing_raise": "面对加注（待行动）",
}

# 8/9 人桌新位置缺图时的回退（按牌力紧度取相邻位置）
FALLBACK_MAP = {
    "UTG+1": "UTG",
    "UTG+2": "UTG",
    "MP": "HJ",
    "LJ": "CO",
}


@lru_cache(maxsize=1)
def load_charts() -> dict:
    with open(_DATA, encoding="utf-8") as fh:
        return json.load(fh)


def situation_codes(position: str, situation: str) -> list[str]:
    """返回某位置某情形的手牌代码列表；新位置缺图时回退。"""
    charts = load_charts()
    if situation in ("defend_SB", "defend_BB"):
        return charts.get(situation, [])
    section = charts.get(situation, {})
    codes = section.get(position)
    if codes is None and position in FALLBACK_MAP:
        codes = section.get(FALLBACK_MAP[position])
    return codes or []


def situation_range(position: str, situation: str):
    """展开为具体组合列表；空图返回空列表。"""
    codes = situation_codes(position, situation)
    if not codes:
        return []
    return expand_range(",".join(codes))


def facing_raise_range(position: str):
    """尚未行动的位置面对加注的估计范围 = 跟注图 ∪ 再加注图（含回退）。"""
    charts = load_charts()
    call_pos = position if position in charts.get("call", {}) else FALLBACK_MAP.get(position, position)
    three_pos = position if position in charts.get("three_bet", {}) else FALLBACK_MAP.get(position, position)
    call = charts.get("call", {}).get(call_pos, [])
    three = charts.get("three_bet", {}).get(three_pos, [])
    codes = [c for c in call if c not in three] + three
    if not codes:
        return []
    return expand_range(",".join(codes))
