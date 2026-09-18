"""对手建模（第二层）：玩家画像统计 + 贝叶斯式范围收窄。

数据文件 backend/data/opponents.json。27：公开部署时多人共用本服务，
具名档案按匿名用户 ID（前端生成的 X-Player-Id）分命名空间存储：

    {
      "users": { "<uid>": {"named": {...}, "_hand_ids": [...]}, ... },
      "_pool":     人群平均（共享，匿名聚合）,
      "_pool_pos": 人群位置统计（共享）
    }

无名对手进人群位置池；具名档案只属于创建它的用户，互不可见。
幂等键 _hand_ids 独立于统计窗口保存（29），上限 5000、裁剪保留
最近 4000——超出窗口的极旧手牌可能被重复计入，这是明确声明的保留策略。
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
            data = json.loads(_DATA.read_text(encoding="utf-8"))
            return _migrate(data)
        except (json.JSONDecodeError, OSError):
            pass
    return {"users": {}}


def _migrate(data: dict) -> dict:
    """旧版（named 平铺在顶层、_seen 平铺）迁移到 users 命名空间（27/39）。

    迁移必须幂等且消费旧键：顶层 legacy 与 _seen 在并入 users 后删除，
    并以 schema=2 标记完成——否则 reset_user 删除 users.local 后，下次
    读取又从残留的顶层键"复活"旧档案。
    """
    if not isinstance(data, dict):
        return {"users": {}, "schema": 2}
    if data.get("schema") == 2 and isinstance(data.get("users"), dict):
        return data
    data.setdefault("users", {})
    legacy = {k: v for k, v in data.items()
              if k not in ("users", "_pool", "_pool_pos", "schema")
              and not k.startswith("_") and isinstance(v, dict)}
    if legacy or "_seen" in data:
        local = data["users"].setdefault("local", {"named": {}, "_hand_ids": []})
        local.setdefault("named", {}).update(legacy)
        seen = data.pop("_seen", None)
        if isinstance(seen, list):
            ids = local.setdefault("_hand_ids", [])
            ids.extend(s for s in seen if isinstance(s, str))
    for k in legacy:
        data.pop(k)
    data["schema"] = 2
    return data


def _user(data: dict, uid: str) -> dict:
    return data["users"].setdefault(uid, {"named": {}, "_hand_ids": []})


def _clean_uid(uid) -> str:
    """用户 ID 只允许安全字符，防注入存储键；非法回退 local。"""
    t = str(uid or "").strip()[:64]
    return t if t and all(ch.isalnum() or ch in "-_" for ch in t) else "local"


def _clean_name(name) -> str:
    """对手代号约束（31）：去空白、限长 32；下划线开头是保留命名空间，
    归为无名（走人群池），防止撞存储元数据键。"""
    t = str(name or "").strip()[:32]
    return "" if t.startswith("_") else t


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


def record_hand(config, ops, names: dict, intel: dict, hero_index=None,
                hand_id=None, uid="local") -> dict:
    """把一手已完成的牌局计入统计。

    intel: decision.player_intel(state) 的输出（每座位行为桶）。
    names: {座位名: 对手代号}；有代名的进该用户的个人档案，其余进人群位置池。
    hand_id: 前端为每手牌生成的唯一标识（14）——内容完全相同的两手
    独立牌局靠它区分；缺省回退到内容签名。同一 hand_id 幂等（不重复计）。
    uid: 匿名用户命名空间（27），档案互不可见，人群池共享。
    返回 {"recorded": bool, "names": [...], "pool": True}。
    """
    with _LOCK:
        return _record_hand_locked(config, ops, names, intel, hero_index,
                                   hand_id, _clean_uid(uid))


def _record_hand_locked(config, ops, names, intel, hero_index,
                        hand_id=None, uid="local") -> dict:
    data = _load()
    user = _user(data, uid)
    # 29：幂等键独立保存，不随统计缓存裁剪；上限 5000 保留最近 4000
    ids = user.setdefault("_hand_ids", [])
    # "id:" 前缀隔离命名空间，避免与内容签名（16 位 hex）碰撞
    sig = ("id:" + str(hand_id)[:64]) if hand_id else hand_signature(config, ops)
    if sig in ids:
        return {"recorded": False, "reason": "重复"}
    ids.append(sig)
    if len(ids) > 5000:
        user["_hand_ids"] = ids[-4000:]

    from .table import positions_for
    positions = config.get("range_positions") or positions_for(int(config.get("player_count", 6)))

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
        name = _clean_name(names.get(pos))

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

        # 具名档案：翻前主动投入才计入 VPIP/PFR/3bet（该用户命名空间内）
        if name:
            named = user.setdefault("named", {})
            prof = named.setdefault(name, {"hands": 0, "vpip": 0, "pfr": 0, "threebet": 0})
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


def get_stats(name: str, uid: str = "local") -> dict:
    """读取某对手的累计统计（限该用户命名空间）；无名返回共享统计池。"""
    data = _load()
    clean = _clean_name(name)
    if not clean:
        p = data.get(POOL, {})
    else:
        user = data.get("users", {}).get(_clean_uid(uid), {})
        p = user.get("named", {}).get(clean, {})
    hands = p.get("hands", 0)
    return {
        "hands": hands,
        "vpip_pct": round(p.get("vpip", 0) * 100 / hands, 1) if hands else None,
        "pfr_pct": round(p.get("pfr", 0) * 100 / hands, 1) if hands else None,
        "threebet_pct": round(p.get("threebet", 0) * 100 / hands, 1) if hands else None,
    }


def summary(uid: str = "local") -> dict:
    """学习进度汇总：共享人群池按位置样本 + 该用户的具名档案。"""
    data = _load()
    pool_pos = data.get("_pool_pos", {})
    user = data.get("users", {}).get(_clean_uid(uid), {})
    named = {k: v for k, v in user.get("named", {}).items() if isinstance(v, dict)}
    total = sum(pp.get("hands", 0) for pp in pool_pos.values())
    return {"total_hands": total, "pool": pool_pos, "named": named}


def reset_user(uid: str) -> None:
    """清空某用户自己的学习数据（档案 + 幂等键）；共享人群池保留。"""
    with _LOCK:
        data = _load()
        data.get("users", {}).pop(_clean_uid(uid), None)
        _save(data)


def reset_all() -> None:
    """清空全部学习数据（所有用户 + 人群池）；需管理令牌或本机调用。"""
    _save({})
