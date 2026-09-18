"""胜率引擎与 API 测试。

已知基准：AA 对 KK 翻前约 81.9% 胜、0.5% 平（公开常识数据，
用 20 万次模拟 + 固定种子校验，容差 ±2 个百分点）。
"""

from fastapi.testclient import TestClient

from backend.app import app
from backend.equity import EquityError, simulate

client = TestClient(app)


def test_known_matchup_aa_vs_kk():
    r = simulate(["As", "Ah"], [{"type": "range", "text": "KK"}], [], 200_000, seed=42)
    assert 79.5 <= r["win"] <= 84.0
    assert r["tie"] < 1.5
    assert abs(r["win"] + r["tie"] + r["lose"] - 100) < 0.02


def test_seed_makes_result_deterministic():
    a = simulate(["As", "Kh"], [{"type": "hand", "cards": ["Qd", "Qc"]}], [], 5_000, seed=7)
    b = simulate(["As", "Kh"], [{"type": "hand", "cards": ["Qd", "Qc"]}], [], 5_000, seed=7)
    for key in ("win", "tie", "lose"):
        assert a[key] == b[key]


def test_royal_flush_beats_quads_on_full_board():
    board = ["As", "Ks", "Qs", "4d", "4c"]
    r = simulate(["Js", "Ts"], [{"type": "hand", "cards": ["Ah", "Ad"]}], board, 1_000, seed=1)
    assert r["exact"] is True          # 河牌圈固定手牌走精确枚举
    assert r["win"] == 100.0 and r["lose"] == 0.0


def test_exact_turn_enumeration():
    # 转牌圈：hero 顶对 A vs 口袋 KK。剩余 44 张河牌里只有 Kc 让 villain 成
    # 三条反超（Kd 在 hero 手里、Ks/Kh 在 villain 手里），精确枚举应得 1/44 落败
    board = ["Ah", "7s", "2d", "9c"]
    r = simulate(["Ac", "Kd"], [{"type": "hand", "cards": ["Ks", "Kh"]}], board, 5_000, seed=1)
    assert r["exact"] is True and r["iterations"] == 44
    assert r["lose"] == round(100 / 44, 2)
    assert abs(r["win"] + r["tie"] + r["lose"] - 100) < 0.02


def test_duplicate_card_rejected():
    try:
        simulate(["As", "As"], [{"type": "range", "text": "KK"}], [], 1_000)
    except EquityError as e:
        assert "重复" in str(e)
    else:
        raise AssertionError("应检测到重复牌")


def test_bad_board_count_rejected():
    for n in (1, 2):
        board = ["As", "Ks", "Qs", "4d", "4c"][:n]
        try:
            simulate(["Js", "Ts"], [{"type": "hand", "cards": ["Ah", "Ad"]}], board, 1_000)
        except EquityError as e:
            assert "公共牌" in str(e)
        else:
            raise AssertionError(f"{n} 张公共牌应被拒绝")


def test_api_success_and_sum():
    r = client.post("/api/equity", json={
        "hero": ["As", "Ad"],
        "villains": [{"type": "range", "text": "KK"}],
        "board": [],
        "iterations": 20_000,
        "seed": 3,
    })
    assert r.status_code == 200
    data = r.json()
    assert abs(data["win"] + data["tie"] + data["lose"] - 100) < 0.02
    assert data["engine"] in ("treys", "eval7")


def test_api_rejects_duplicate():
    r = client.post("/api/equity", json={
        "hero": ["As", "As"],
        "villains": [{"type": "hand", "cards": ["Kh", "Kd"]}],
        "board": [],
    })
    assert r.status_code == 400
    assert "重复" in r.json()["detail"]


def test_api_rejects_bad_board():
    r = client.post("/api/equity", json={
        "hero": ["As", "Ad"],
        "villains": [{"type": "hand", "cards": ["Kh", "Kd"]}],
        "board": ["2c", "3c"],
    })
    assert r.status_code == 400


def test_api_rejects_iterations_out_of_range():
    r = client.post("/api/equity", json={
        "hero": ["As", "Ad"],
        "villains": [{"type": "hand", "cards": ["Kh", "Kd"]}],
        "board": [],
        "iterations": 100,
    })
    assert r.status_code == 400


def test_index_page_served():
    # 首页是跳转壳，应用本体在 /app.html
    r = client.get("/")
    assert r.status_code == 200
    assert "location.replace" in r.text
    r2 = client.get("/app.html")
    assert r2.status_code == 200
    assert "锦标赛逐手记录" in r2.text   # 5.1：标题改为锦标赛逐手记录与局面推演
