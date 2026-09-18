"""Review 52-58: replay real v2 actions, advice, recording and malformed requests."""
import copy

import pytest
from fastapi.testclient import TestClient
from backend.app import app
from backend import opponents

client = TestClient(app)


def payload(n=6, no_sb=False):
    seats = list(range(2 if no_sb else 1, n + (2 if no_sb else 1)))
    button = seats[-1]
    hero = button if n in (2, 3) else seats[1 if no_sb else 2]
    return {"protocol_version": 2, "hand_id": f"test-{n}-{no_sb}",
            "hero_cards": ["As", "Ad"], "iterations": 1000, "seed": 7, "ops": [],
            "context": {"session_id": "s-regression", "capacity": 9,
                        "hero_occupant_id": f"p{hero}",
                        "participants": [{"seat_id": s, "occupant_id": f"p{s}", "starting_chips": 10000} for s in seats],
                        "button_seat_id": button,
                        "small_blind_seat_id": None if no_sb else button if n == 2 else seats[0],
                        "big_blind_seat_id": seats[0] if n == 2 or no_sb else seats[1],
                        "blinds": {"sb": 100, "bb": 200, "ante_each": 0},
                        "learning_enabled": False, "profile_names": {}, "icm": {"scope": "off", "payouts": []}}}


def post(path, body):
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("n", range(2, 10))
def test_every_actual_player_count_advice_four_streets_and_record(n):
    body = payload(n)
    advice = post("/api/hand/v2/advice", body)
    assert advice["advice"]["opponents"] and advice["advice"]["recommendation"]["mix"]
    deals = [["2c", "3d", "7h"], ["9c"], ["Td"]]
    seen = []
    for _ in range(60):
        view = post("/api/hand/v2/view", body)
        if view["hand_over"]:
            break
        actor = view["actor_seat_id"]
        if actor is not None:
            body["ops"].append({"op": "action", "seat_id": actor, "type": "call" if view["to_call"] else "check"})
        else:
            cards = deals.pop(0)
            seen.append(view["board_dealing_count"])
            assert view["board_dealing_count"] == len(cards)
            body["ops"].append({"op": "board", "cards": cards})
    assert view["hand_over"] and seen == [3, 1, 1]
    assert len(view["board"]) == 5
    assert all(seat["stack"] is None for seat in view["seats"])
    assert all(row["source"] == "unknown" for row in view["settlement_preview"]["rows"])
    body["context"]["learning_enabled"] = True
    result = post("/api/hand/v2/record", body)
    assert result["recorded"]["recorded"] is True
    assert post("/api/hand/v2/record", body)["recorded"]["recorded"] is False


def test_no_small_blind_advice_names_and_nominal_bb(monkeypatch):
    body = payload(5, no_sb=True)
    body["context"]["learning_enabled"] = True
    body["context"]["profile_names"] = {"p2": "BB-name", "p6": "BTN-name"}
    reads = []
    monkeypatch.setattr(opponents, "get_stats", lambda name, **kw: reads.append(name) or None)
    view = post("/api/hand/v2/advice", body)
    rows = {row["seat_id"]: row for row in view["advice"]["opponents"]}
    assert rows[2]["pos"] == "BB" and rows[6]["pos"] == "BTN"
    assert set(reads) == {"BB-name", "BTN-name"}
    assert all("None" not in m["label"] for m in view["advice"]["recommendation"]["mix"])
    assert view["advice"]["m_value"] == 50
    body["context"]["learning_enabled"] = False
    reads.clear()
    post("/api/hand/v2/advice", body)
    assert reads == []


def test_icm_is_starting_snapshot_keyed_by_occupant():
    body = payload(3)
    body["context"]["icm"] = {"scope": "final_table", "payouts": [500, 300, 100]}
    icm = post("/api/hand/v2/view", body)["icm"]
    assert icm["snapshot"] == "hand-start"
    assert {r["occupant_id"] for r in icm["rows"]} == {"p1", "p2", "p3"}
    assert [r["equity"] for r in icm["rows"]] == [300, 300, 300]


@pytest.mark.parametrize("field,value", [("ops", [None]), ("ops", {}), ("hero_cards", 42),
    ("iterations", "1000"), ("iterations", 10), ("iterations", 1000001), ("seed", []),
    ("ops", [{"op": "action", "seat_id": 3, "type": "raise", "to": 400.5}]),
    ("ops", [{"op": "action", "seat_id": True, "type": "call"}])])
def test_malformed_payload_is_4xx(field, value):
    body = payload()
    body[field] = value
    response = client.post("/api/hand/v2/view", json=body)
    assert 400 <= response.status_code < 500


@pytest.mark.parametrize("icm", [42, [], {"scope": "off", "payouts": 1}, {"scope": "final_table", "payouts": [True]}])
def test_malformed_icm_is_4xx(icm):
    body = payload()
    body["context"]["icm"] = copy.deepcopy(icm)
    assert 400 <= client.post("/api/table/prepare", json=body).status_code < 500


def test_concurrent_replay_uses_same_cards_and_blinds():
    from concurrent.futures import ThreadPoolExecutor
    from backend.table import replay_context
    from backend.table_context import validate_context
    body = payload(6)
    context = validate_context(body["context"])
    def replay(_):
        state, plan, dealt = replay_context(context, body["hero_cards"], [])
        return list(state.stacks), dealt, plan["range_positions"]
    baseline = replay(0)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(result == baseline for result in pool.map(replay, range(32)))
