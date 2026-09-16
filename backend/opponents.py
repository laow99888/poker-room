"""对手建模（第二层）：玩家画像统计 + 贝叶斯式范围收窄。

数据文件 backend/data/opponents.json，按对手代号累计：
    hands / vpip / pfr / threebet / 见过的手牌签名（去重）

无名对手进入统计池 "_pool"（人群平均），作为冷启动先验。
"""

import hashlib
import json
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "data" / "opponents.json"
POOL = "_pool"


def _load() -> dict:
    if _DATA.exists():
        try:
            return json.loads(_DATA.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save(data: dict) -> None:
    _DATA.parent.mkdir(parents=True, exist_ok=True)
    _DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def hand_signature(config: dict, ops: list) -> str:
    """整手牌的稳定签名：同一手重复提交不会重复计入统计。"""
    raw = json.dumps({"c": config, "o": ops}, sort_keys=True, ensure_ascii=False)
    import hashlib
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def record_hand(config, ops, names: dict, lines: dict, hero_index=None) -> dict:
    """把一手已完成的牌局计入对手档案。返回 {记入了谁}。

    names: {座位索引字符串: 对手代号}；空代号进统计池。
    lines: {座位索引: 'rfi'|'three_bet'|'call'|'pending'}（由 decision 分类）。
    """
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
    recorded = []
    for seat_str, line in lines.items():
        if hero_index is not None and str(seat_str) == str(hero_index):
            continue
        pos_name = positions[int(seat_str)]
        name = (names.get(pos_name) or names.get(str(seat_str)) or "").strip() or POOL
        p = data.setdefault(name, {"hands": 0, "vpip": 0, "pfr": 0, "threebet": 0})
        p["hands"] += 1
        if line in ("rfi", "three_bet", "call"):
            p["vpip"] += 1
        if line in ("rfi", "three_bet"):
            p["pfr"] += 1
        if line == "three_bet":
            p["threebet"] += 1
        recorded.append(name)

    _save(data)
    return {"recorded": True, "names": sorted(set(recorded))}


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
