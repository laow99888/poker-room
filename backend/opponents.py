"""对手建模（第二层）：玩家画像统计 + 贝叶斯式范围收窄。

数据文件 backend/data/opponents.json，按对手代号累计：
    hands / vpip / pfr / threebet / 见过的手牌签名（去重）

无名对手进入统计池 "_pool"（人群平均），作为冷启动先验。
"""

import hashlib
import json
import os
import threading
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "data" / "opponents.json"
POOL = "_pool"
_LOCK = threading.Lock()   # 公网多人同时记录手牌时串行化读改写


def _load() -> dict:
    if _DATA.exists():
        try:
            return json.loads(_DATA.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save(data: dict) -> None:
    """先写临时文件再原子替换，避免并发读到写了一半的 JSON。"""
    _DATA.parent.mkdir(parents=True, exist_ok=True)
    tmp = _DATA.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, _DATA)


def hand_signature(config: dict, ops: list) -> str:
    """整手牌的稳定签名：同一手重复提交不会重复计入统计。"""
    raw = json.dumps({"c": config, "o": ops}, sort_keys=True, ensure_ascii=False)
    import hashlib
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def record_hand(config, ops, names: dict, intel: dict, hero_index=None) -> dict:
    """把一手已完成的牌局计入统计。

    intel: decision.player_intel(state) 的输出（每座位行为桶）。
    names: {座位名: 对手代号}；有代名的进个人档案，其余进人群位置池。
    返回 {"recorded": bool, "names": [...], "pool": True}。
    """
    with _LOCK:
        return _record_hand_locked(config, ops, names, intel, hero_index)


def _record_hand_locked(config, ops, names, intel, hero_index) -> dict:
    data = _load()
    seen = data.setdefault("_seen", [])
    sig = hand_signature(config, ops)
    if sig in seen:
        return {"recorded": False, "reason": "重复"}
    seen.append(sig)
    if len(seen) > 800:
        data["_seen"] = seen[-500:]

    from .table import positions_for
    positions = positions_for(int(config.get("player_count", 6)))

    pool_pos = data.setdefault("_pool_pos", {})
    pool = data.setdefault(POOL, {"hands": 0, "vpip": 0, "pfr": 0, "threebet": 0})
    recorded = []

    for i_str, info in intel.items():
        i = int(i_str)
        if i == hero_index:
            continue
        pos = positions[i]
        line = info.get("line")
        kind = info.get("kind")
        name = (names.get(pos) or "").strip()

        # 人群位置统计（所有未弃牌相关行为都计入）
        pp = pool_pos.setdefault(pos, {"hands": 0, "opens": 0, "limps": 0,
                                       "threebets": 0, "call_raise": 0, "fold_raise": 0})
        pp["hands"] += 1
        if kind == "open":
            pp["opens"] += 1
        elif kind == "threebet":
            pp["threebets"] += 1
        elif kind == "limp":
            pp["limps"] += 1
        elif kind == "call_raise":
            pp["call_raise"] += 1
        elif kind == "fold_raise":
            pp["fold_raise"] += 1

        # 具名档案：翻前主动投入才计入 VPIP/PFR/3bet
        if name:
            prof = data.setdefault(name, {"hands": 0, "vpip": 0, "pfr": 0, "threebet": 0})
            prof["hands"] += 1
            if kind in ("open", "threebet", "limp", "call_raise"):
                prof["vpip"] += 1
            if kind in ("open", "threebet"):
                prof["pfr"] += 1
            if kind == "threebet":
                prof["threebet"] += 1
            recorded.append(name)

    _save(data)
    return {"recorded": True, "names": sorted(set(recorded)), "pool": True}


def get_pool(pos: str, min_hands: int = 10):
    """某位置的人群统计；样本不足返回 None。"""
    data = _load()
    pp = data.get("_pool_pos", {}).get(pos)
    if not pp or pp.get("hands", 0) < min_hands:
        return None
    hands = pp["hands"]
    return {
        "hands": hands,
        "open_pct": round(pp.get("opens", 0) * 100 / hands, 1),
        "limp_pct": round(pp.get("limps", 0) * 100 / hands, 1),
        "threebet_pct": round(pp.get("threebets", 0) * 100 / hands, 1),
        "call_raise_pct": round(pp.get("call_raise", 0) * 100 / hands, 1),
        "fold_raise_pct": round(pp.get("fold_raise", 0) * 100 / hands, 1),
    }


def get_stats(name: str) -> dict:
    """读取某对手的累计统计；无名返回统计池。"""
    data = _load()
    p = data.get((name or "").strip() or POOL, {})
    hands = p.get("hands", 0)
    return {
        "hands": hands,
        "vpip_pct": round(p.get("vpip", 0) * 100 / hands, 1) if hands else None,
        "pfr_pct": round(p.get("pfr", 0) * 100 / hands, 1) if hands else None,
        "threebet_pct": round(p.get("threebet", 0) * 100 / hands, 1) if hands else None,
    }


def summary() -> dict:
    """学习进度汇总：人群池按位置样本 + 具名档案。"""
    data = _load()
    pool_pos = data.get("_pool_pos", {})
    named = {k: v for k, v in data.items()
             if not k.startswith("_") and isinstance(v, dict)}
    total = sum(pp.get("hands", 0) for pp in pool_pos.values())
    return {"total_hands": total, "pool": pool_pos, "named": named}


def reset_all() -> None:
    """清空全部学习数据（人群池 + 具名档案 + 去重签名）。"""
    _save({})
