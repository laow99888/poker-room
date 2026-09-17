"""本地 FastAPI 应用：/api/equity + /api/hand/* + 静态前端，全程离线（绑定 127.0.0.1）。"""

from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import decision, equity, opponents
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


class HandIn(BaseModel):
    config: HandConfigIn
    hero_pos: str
    hero_cards: List[str]
    ops: List[dict] = []
    iterations: int = 50_000
    seed: Optional[int] = None
    names: dict = {}        # 座位 → 对手代号（用于累计统计）


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
    except (equity.EquityError, InvalidHandError) as exc:
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
    names: dict = {}        # 座位 → 对手代号（用于累计统计）


def _config_dump(config: HandConfigIn) -> dict:
    return config.model_dump() if hasattr(config, "model_dump") else config.dict()


def _replay_or_400(payload: HandIn):
    try:
        return replay_state(_config_dump(payload.config), payload.hero_pos,
                            payload.hero_cards, payload.ops)
    except TableError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/hand/view")
def hand_view_api(payload: HandIn):
    state, hero_index, dealt = _replay_or_400(payload)
    from .table import hand_view
    return hand_view(state, hero_index, dealt)


@app.post("/api/hand/advice")
def hand_advice_api(payload: HandIn):
    if not 1_000 <= payload.iterations <= 1_000_000:
        raise HTTPException(400, "模拟次数需在 1,000 到 1,000,000 之间")
    state, hero_index, dealt = _replay_or_400(payload)
    from .table import hand_view
    view = hand_view(state, hero_index, dealt)
    if view["hand_over"]:
        raise HTTPException(400, "这手牌已经结束")
    if view["actor"] != payload.hero_pos:
        raise HTTPException(400, f"还没轮到你行动（当前：{view['actor'] or '需要发牌'}）")
    try:
        advice = decision.advice_for(state, hero_index, payload.iterations,
                                     payload.seed, payload.names)
    except TableError as exc:
        raise HTTPException(400, str(exc))
    return {**view, "advice": advice}


@app.post("/api/stats/record")
def stats_record_api(payload: HandIn):
    """手牌结束后调用：把本手计入对手档案（按签名去重，可安全重复提交）。"""
    state, hero_index, _ = _replay_or_400(payload)
    if state.status:
        raise HTTPException(400, "手牌尚未结束，无法记录")
    lines = decision._classify_lines(state)
    result = opponents.record_hand(_config_dump(payload.config), payload.ops,
                                   payload.names, lines, hero_index=hero_index)
    return {"recorded": result}


@app.get("/api/stats/{name}")
def stats_get_api(name: str):
    return opponents.get_stats(name)


@app.post("/api/stats/reset")
def stats_reset_api():
    opponents.reset_all()
    return {"reset": True}


@app.get("/api/stats/summary/all")
def stats_summary_api():
    """学习进度：人群池按位置的样本量 + 具名档案列表。"""
    data = opponents.summary()
    return data


_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(_FRONTEND), html=True), name="static")
