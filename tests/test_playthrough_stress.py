"""Seeded full-hand API walks: legal actions, short stacks, runouts and replay."""
import random

import pytest
from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)


@pytest.mark.parametrize("seed", range(120))
def test_full_hand_api_walk(seed):
    rng = random.Random(190900 + seed)
    capacity = (6, 8, 9)[seed % 3]
    seats = sorted(rng.sample(range(1, capacity + 1), rng.randint(2, capacity)))
    button = rng.choice(seats) if len(seats) == 2 else rng.randint(1, capacity)
    order = [s for s in seats if s > button] + [s for s in seats if s <= button]
    sb = button if len(seats) == 2 else order[0]
    bb = order[0] if len(seats) == 2 else order[1]
    if len(seats) > 2 and len(seats) < capacity and seed % 4 == 0:
        sb, bb = None, order[0]
    hero = rng.choice(seats)
    stacks = {s: rng.choice([1, 25, 75, 125, 225, 500, 1000, 10000]) for s in seats}
    for s in rng.sample(seats, 2):
        stacks[s] = 10000
    context = {"session_id": f"stress-{seed}", "capacity": capacity,
               "hero_occupant_id": f"p{hero}",
               "participants": [{"seat_id": s, "occupant_id": f"p{s}", "starting_chips": stacks[s]} for s in seats],
               "button_seat_id": button, "small_blind_seat_id": sb, "big_blind_seat_id": bb,
               "blinds": {"sb": 100, "bb": 200, "ante_each": seed % 2 * 25},
               "learning_enabled": False, "profile_names": {}, "icm": {"scope": "off", "payouts": []}}
    deck = [r + s for r in "23456789TJQKA" for s in "cdhs"]
    rng.shuffle(deck)
    cards = [deck.pop(), deck.pop()]
    body = {"protocol_version": 2, "hand_id": f"hand-{seed}", "context": context, "hero_cards": cards, "ops": []}
    folded, contributions = set(), {}

    def view():
        response = client.post("/api/hand/v2/view", json=body)
        assert response.status_code == 200, (seed, body, response.text)
        return response.json()

    state = view()
    for step in range(150):
        assert {s["seat_id"] for s in state["seats"]} == set(seats)
        assert len(state["board"]) == len(set(state["board"]))
        assert not set(cards).intersection(state["board"])
        if state["hand_over"]:
            break
        assert sum(s["stack"] for s in state["seats"]) + state["pot"] == sum(stacks.values())
        assert all(isinstance(s["stack"], int) and s["stack"] >= 0 for s in state["seats"])
        actor = state["actor_seat_id"]
        if actor is None:
            count = state["board_dealing_count"]
            assert count in [1, 3], (seed, body, state)
            body["ops"].append({"op": "board", "cards": [deck.pop() for _ in range(count)]})
        else:
            assert actor not in folded
            own = next(s for s in state["seats"] if s["seat_id"] == actor)
            if actor == hero and state["can_fold"] and body["ops"]:
                quick = client.post("/api/table/quick-fold", json=body)
                assert quick.status_code == 200, quick.text
                row = next(r for r in quick.json()["rows"] if r["seat_id"] == hero)
                assert row["suggested_chips"] == own["stack"]
            roll = rng.random()
            action = {"op": "action", "seat_id": actor}
            if state["can_fold"] and roll < .20:
                action["type"] = "fold"
                folded.add(actor)
                contributions[actor] = own["stack"]
            elif state.get("min_raise_to") is not None and state.get("max_raise_to") is not None and roll > .70:
                action.update(type="raise", to=rng.choice([state["min_raise_to"], state["max_raise_to"]]))
            else:
                action["type"] = "call" if state.get("to_call", 0) > 0 else "check"
            body["ops"].append(action)
        state = view()
        if step % 9 == 0:
            assert view() == state, "identical replay changed the state"
    else:
        pytest.fail(f"hand did not terminate: {seed}, {body}")

    rows = {r["seat_id"]: r for r in state["settlement_preview"]["rows"]}
    showdown = len(seats) - len(folded) >= 2
    for seat in seats:
        row = rows[seat]
        if showdown and seat not in folded:
            assert row["source"] == "unknown" and row["suggested_chips"] is None
        else:
            assert row["source"] == "verified"
            assert isinstance(row["suggested_chips"], int) and row["suggested_chips"] >= 0
        if seat in folded:
            assert row["suggested_chips"] == contributions[seat]
    if not showdown:
        assert sum(r["suggested_chips"] for r in rows.values()) == sum(stacks.values())
