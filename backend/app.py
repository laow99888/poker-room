"""本地 FastAPI 应用：/api/equity + /api/hand/* + 静态前端，全程离线（绑定 127.0.0.1）。"""

import math
import os
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from . import decision, equity, opponents
from .icm import ICMError, icm_equities
from .ranges import InvalidHandError
from .table import TableError, replay_state

app = FastAPI(title="大魔丸 · 德州扑克离线决策辅助", docs_url=None, redoc_url=None)


class VillainIn(BaseModel):
    type: str = "hand"  # "hand" / "range" / "combos"
    cards: List[str] = []
    text: str = ""
    combos: List[List[str]] = []


class EquityIn(BaseModel):
    hero: List[str]
    villains: List[VillainIn]
    board: List[str] = []
    iterations: int = 100_000
    seed: Optional[int] = None  # 仅供测试复现，界面不传


class HandConfigIn(BaseModel):
    player_count: int = 6   # 6 / 8 / 9
    sb: int
    bb: int
    ante: int = 0
    stacks: Optional[List[int]] = None
    payouts: List[float] = []   # 决赛桌奖金结构（可选），如 [500, 300, 200]

    @field_validator("payouts")
    @classmethod
    def _finite_payouts(cls, v):
        # 31：奖金必须是有限数值，Infinity/NaN 会让 ICM 计算产生 500
        for p in v:
            if not math.isfinite(p):
                raise ValueError(f"奖金必须为有限数值，收到 {p!r}")
        return v


def _dump(v) -> dict:
    return v.model_dump() if hasattr(v, "model_dump") else v.dict()


@app.post("/api/equity")
def equity_api(payload: EquityIn):
    if not 1_000 <= payload.iterations <= 1_000_000:
        raise HTTPException(400, "模拟次数需在 1,000 到 1,000,000 之间")
    try:
        return equity.simulate(
            hero=payload.hero,
            villains=[_dump(v) for v in payload.villains],
            board=payload.board,
            iterations=payload.iterations,
            seed=payload.seed,
        )
    except (equity.EquityError, InvalidHandError, ValueError, TypeError,
            KeyError, IndexError) as exc:
        raise HTTPException(400, str(exc))


@app.middleware("http")
async def no_cache_static(request, call_next):
    """本地工具：静态资源禁缓存，避免改版后浏览器拿旧文件。"""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response


class HandIn(BaseModel):
    config: HandConfigIn
    hero_pos: str
    hero_cards: List[str]
    ops: List[dict] = []
    iterations: int = 50_000
    seed: Optional[int] = None
    names: Dict[str, str] = {}      # 31：座位 → 对手代号，值必须是字符串
    hand_id: Optional[str] = None   # 前端生成的手牌唯一标识（14：记录幂等键）


def _config_dump(config: HandConfigIn) -> dict:
    return config.model_dump() if hasattr(config, "model_dump") else config.dict()


def _replay_or_400(payload: HandIn):
    try:
        return replay_state(_config_dump(payload.config), payload.hero_pos,
                            payload.hero_cards, payload.ops)
    except (TableError, ValueError, TypeError, KeyError, IndexError) as exc:
        # 非法输入/非法操作是客户端错误：必须稳定 4xx，不允许逃逸成 500
        raise HTTPException(400, str(exc))


def _attach_icm(view: dict, config: HandConfigIn) -> None:
    """配置了奖金结构时，把 ICM 奖金期望挂到视图上（解析失败静默跳过）。

    12：用手前筹码快照（config.stacks，缺省 50BB）而非下注后的剩余筹码——
    在池的筹码仍在争夺中，全下者剩余为 0 也不代表出局。ICM 语义固定为
    "这手牌开始时的记分牌值多少奖金"，因此也不会再出现 0 筹码报错。
    """
    payouts = [float(p) for p in (config.payouts or [])]
    if not payouts or not any(p > 0 for p in payouts):
        return
    n = int(config.player_count)
    if any(p < 0 for p in payouts) or len(payouts) > n:
        view["icm"] = {"error": "奖金结构无效：需为非负数，且名次数不超过人数"}
        return
    stacks = ([int(x) for x in config.stacks] if config.stacks
              else [config.bb * 50] * n)
    try:
        eq = icm_equities(stacks, payouts)
    except ICMError as exc:
        view["icm"] = {"error": str(exc)}
        return
    pool = float(sum(payouts))
    rows = [{"pos": view["seats"][i]["pos"], "stack": stacks[i],
             "hero": view["seats"][i].get("is_hero", False),
             "equity": round(eq[i], 1), "pct": round(eq[i] * 100 / pool, 1)}
            for i in range(min(n, len(view["seats"])))]
    rows.sort(key=lambda r: -r["equity"])
    view["icm"] = {"payouts": payouts, "total": pool, "rows": rows,
                   "snapshot": "hand-start"}


@app.post("/api/hand/view")
def hand_view_api(payload: HandIn):
    state, hero_index, dealt = _replay_or_400(payload)
    from .table import hand_view
    view = hand_view(state, hero_index, dealt)
    _attach_icm(view, payload.config)
    return view


@app.post("/api/hand/advice")
def hand_advice_api(payload: HandIn, request: Request):
    if not 1_000 <= payload.iterations <= 1_000_000:
        raise HTTPException(400, "模拟次数需在 1,000 到 1,000,000 之间")
    state, hero_index, dealt = _replay_or_400(payload)
    from .table import hand_view
    view = hand_view(state, hero_index, dealt)
    _attach_icm(view, payload.config)
    if view["hand_over"]:
        raise HTTPException(400, "这手牌已经结束")
    if view["actor"] != payload.hero_pos:
        raise HTTPException(400, f"还没轮到你行动（当前：{view['actor'] or '需要发牌'}）")
    try:
        advice = decision.advice_for(state, hero_index, payload.iterations,
                                     payload.seed, payload.names,
                                     uid=_player_id(request))
    except TableError as exc:
        raise HTTPException(400, str(exc))
    return {**view, "advice": advice}


def _player_id(request: Request) -> str:
    """匿名用户命名空间（27）：前端生成的随机 ID，仅用于隔离数据归属，
    不是身份认证；缺失或非法回退 "local"（本地单人模式）。"""
    return opponents._clean_uid(request.headers.get("X-Player-Id"))


@app.post("/api/stats/record")
def stats_record_api(payload: HandIn, request: Request):
    """手牌结束后调用：把本手计入对手档案（按 hand_id/签名去重，可安全重复提交）。"""
    state, hero_index, _ = _replay_or_400(payload)
    if state.status:
        raise HTTPException(400, "手牌尚未结束，无法记录")
    intel = decision.player_intel(state)
    result = opponents.record_hand(_config_dump(payload.config), payload.ops,
                                   payload.names, intel, hero_index=hero_index,
                                   hand_id=payload.hand_id,
                                   uid=_player_id(request))
    return {"recorded": result}


@app.get("/api/stats/{name}")
def stats_get_api(name: str, request: Request):
    return opponents.get_stats(name, uid=_player_id(request))


@app.post("/api/stats/reset")
def stats_reset_api(request: Request):
    """27：清空范围由部署模式决定，不依赖 client host 判断——
    Nginx 反代下所有请求的来源地址都是 127.0.0.1，按地址放行等于向
    公网开放全清。
    - 未配置 POKER_ADMIN_TOKEN：本地单人模式，重置=全清（兼容原行为）；
    - 已配置：公开多人模式，重置只清当前用户自己的数据；
      持 X-Admin-Token 才能全清（所有用户+人群池）。
    公开部署必须设置 POKER_ADMIN_TOKEN。
    """
    token = os.environ.get("POKER_ADMIN_TOKEN", "")
    if not token or request.headers.get("X-Admin-Token") == token:
        opponents.reset_all()
        return {"reset": "all"}
    opponents.reset_user(_player_id(request))
    return {"reset": "user"}


@app.get("/api/stats/summary/all")
def stats_summary_api(request: Request):
    """学习进度：共享人群池按位置的样本量 + 当前用户的具名档案列表。"""
    data = opponents.summary(uid=_player_id(request))
    return data


_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(_FRONTEND), html=True), name="static")
