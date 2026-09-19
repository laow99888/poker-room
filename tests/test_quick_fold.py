"""Quick records use PokerKit deductions, without fabricating other players' results."""
import pytest
from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)


def context(capacity=6, hero=1):
    return {"session_id": "quick", "capacity": capacity, "hero_occupant_id": f"P{hero}",
            "participants": [{"seat_id": n, "occupant_id": f"P{n}", "starting_chips": 10000}
                             for n in range(1, capacity + 1)],
            "button_seat_id": capacity, "small_blind_seat_id": 1, "big_blind_seat_id": 2,
            "blinds": {"sb": 100, "bb": 200, "ante_each": 25},
            "learning_enabled": False, "profile_names": {}, "icm": {"scope": "off", "payouts": []}}


def post(ctx, ops=None, cards=None):
    return client.post("/api/table/quick-fold", json={"protocol_version": 2, "hand_id": "h-quick",
                       "context": ctx, "hero_cards": cards, "ops": ops or []})


@pytest.mark.parametrize("capacity", [6, 8, 9])
@pytest.mark.parametrize("hero,expected", [(1, 9875), (2, 9775), (3, 9975)])
def test_no_cards_needed_and_only_hero_balance_is_known(capacity, hero, expected):
    response = post(context(capacity, hero))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["hand_id"] == "h-quick"
    for row in data["rows"]:
        assert row["suggested_chips"] == (expected if row["seat_id"] == hero else None)
        assert row["source"] == ("verified" if row["seat_id"] == hero else "unknown")


def test_heads_up_and_missing_small_blind_use_actual_role_mapping():
    c = context(2)
    c.update(button_seat_id=1)
    assert post(c).json()["rows"][1]["suggested_chips"] == 9875
    c = context(6, 2)
    c["participants"] = c["participants"][1:]
    c.update(small_blind_seat_id=None)
    rows = post(c).json()["rows"]
    assert next(r for r in rows if r["seat_id"] == 2)["suggested_chips"] == 9775


@pytest.mark.parametrize("chips", [1, 25, 100, 125])
def test_forced_all_in_cannot_be_recorded_as_fold(chips):
    c = context()
    c["participants"][0]["starting_chips"] = chips
    response = post(c)
    assert response.status_code == 400


def test_previous_call_and_known_opponent_fold_are_preserved():
    c = context(hero=3)
    ops = [{"op": "action", "seat_id": 3, "type": "call"},
           {"op": "action", "seat_id": 4, "type": "raise", "to": 600},
           *[{"op": "action", "seat_id": n, "type": "fold"} for n in [5, 6, 1]],
           {"op": "action", "seat_id": 2, "type": "call"}]
    response = post(c, ops, ["As", "Ad"])
    assert response.status_code == 200, response.text
    rows = {r["seat_id"]: r for r in response.json()["rows"]}
    assert rows[3]["suggested_chips"] == 9775
    assert rows[1]["suggested_chips"] == 9875
    assert rows[4]["suggested_chips"] is None
    assert rows[2]["suggested_chips"] is None
    ops.append({"op": "action", "seat_id": 3, "type": "fold"})
    after_fold = post(c, ops, ["As", "Ad"]).json()["rows"]
    assert [(r["seat_id"], r["suggested_chips"], r["source"]) for r in after_fold] == [
        (r["seat_id"], r["suggested_chips"], r["source"]) for r in response.json()["rows"]]
    assert next(r for r in after_fold if r["seat_id"] == 3)["reason"] == "folded"


def test_out_of_turn_after_actions_and_invalid_payload_are_rejected():
    c = context(hero=3)
    ops = [{"op": "action", "seat_id": 3, "type": "call"}]
    assert post(c, ops, ["As", "Ad"]).status_code == 400
    assert post(c, ops).status_code == 400
    for invalid in [None, {}, [None], [{"op": "action", "seat_id": 3, "type": "raise", "to": 200.5}]]:
        response = client.post("/api/table/quick-fold", json={"protocol_version": 2,
                               "context": c, "hero_cards": ["As", "Ad"], "ops": invalid})
        assert response.status_code == 400
    assert client.post("/api/table/quick-fold", json={"protocol_version": 1, "context": c}).status_code == 400
