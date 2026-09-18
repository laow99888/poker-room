"""Read-only HTTP reproduction for the 2026-09-19 GLM delivery review.

Run from any directory: python -X utf8 path/to/backend-probe.py
Writes backend-probe.json beside this script. No development server is started.
Statistics reads are mocked; statistics storage access raises if attempted.
Results retain raw responses rather than stopping at the first failed check.
"""

from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from backend.app import app


def context(count=6, no_small_blind=False):
    seats = list(range(2, count + 2)) if no_small_blind else list(range(1, count + 1))
    hero = seats[1] if no_small_blind else (2 if count == 2 else 3)
    return {
        "session_id": "review-probe-session",
        "capacity": 9,
        "hero_occupant_id": f"p{hero}",
        "participants": [
            {"seat_id": seat, "occupant_id": f"p{seat}", "starting_chips": 10000}
            for seat in seats
        ],
        "button_seat_id": seats[-1],
        "small_blind_seat_id": None if no_small_blind else (seats[-1] if count == 2 else seats[0]),
        "big_blind_seat_id": seats[0] if count == 2 or no_small_blind else seats[1],
        "blinds": {"sb": 100, "bb": 200, "ante_each": 0},
        "learning_enabled": False,
        "profile_names": {},
        "icm": {"scope": "off", "payouts": []},
    }


def payload(ctx=None):
    return {
        "protocol_version": 2,
        "hand_id": "review-probe-hand",
        "context": context() if ctx is None else ctx,
        "hero_cards": ["As", "Ad"],
        "ops": [],
        "iterations": 1000,
        "seed": 1,
    }


def request(client, path, body):
    try:
        response = client.post(path, json=body)
        try:
            result = response.json()
        except ValueError:
            result = response.text
        return {"path": path, "request": body, "status": response.status_code, "response": result}
    except Exception as exc:
        return {"path": path, "request": body, "exception": f"{type(exc).__name__}: {exc}"}


def object_response(result):
    value = result.get("response")
    return value if isinstance(value, dict) else {}


def main():
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "Capture observed backend behavior; this is evidence, not a passing acceptance suite.",
        "data_isolation": "get_stats/get_pool mocked; _load/_save forbidden; no record/reset endpoint used",
        "short_tables": [],
    }
    with ExitStack() as stack:
        stats = stack.enter_context(patch("backend.opponents.get_stats", return_value=None))
        pool = stack.enter_context(patch("backend.opponents.get_pool", return_value=None))
        load = stack.enter_context(patch("backend.opponents._load", side_effect=RuntimeError("Real statistics reads forbidden")))
        save = stack.enter_context(patch("backend.opponents._save", side_effect=RuntimeError("Real statistics writes forbidden")))
        client = stack.enter_context(TestClient(app, raise_server_exceptions=False))

        for count in (2, 3, 4, 5, 7):
            body = payload(context(count))
            initial = request(client, "/api/hand/v2/view", body)
            actor = object_response(initial).get("actor_seat_id")
            action = request(client, "/api/hand/v2/view", {
                **body, "ops": [{"op": "action", "seat_id": actor, "type": "fold"}],
            })
            advice = request(client, "/api/hand/v2/advice", body)
            report["short_tables"].append({
                "count": count,
                "expected": "Initial view, first legal fold, and hero advice all succeed for 2-9 participants.",
                "initial": initial, "action": action, "advice": advice,
            })

        result = request(client, "/api/hand/v2/advice", payload(context(6, True)))
        view = object_response(result)
        advice = view.get("advice", {})
        report["empty_small_blind"] = {
            "expected": "Nominal BB is 200; use context range positions and return physical seat identities.",
            "request_result": result,
            "view_positions": [{"seat_id": s.get("seat_id"), "range_position": s.get("range_position")}
                               for s in view.get("seats", [])],
            "advice_positions": [{"seat_id": s.get("seat_id"), "pos": s.get("pos")}
                                 for s in advice.get("opponents", [])],
            "recommendation": advice.get("recommendation"),
        }

        ctx = context()
        ctx["icm"] = {"scope": "final_table", "payouts": [500, 300, 200]}
        result = request(client, "/api/hand/v2/view", payload(ctx))
        report["final_table_icm"] = {
            "expected": "Return ICM calculated from starting chips with physical seat IDs.",
            "icm_present": "icm" in object_response(result),
            "request_result": result,
        }

        stats.reset_mock()
        pool.reset_mock()
        ctx = context()
        ctx["learning_enabled"] = True
        ctx["profile_names"] = {"p2": "named-review-probe"}
        result = request(client, "/api/hand/v2/advice", payload(ctx))
        report["named_learning"] = {
            "expected": "Named opponent p2 triggers get_stats when learning is enabled.",
            "get_stats_calls": stats.call_count,
            "get_pool_calls": pool.call_count,
            "request_result": result,
        }

        bad_icm = context()
        bad_icm["icm"] = 7
        cases = [
            ("icm_number", "/api/table/prepare", {"protocol_version": 2, "context": bad_icm}),
            ("null_operation", "/api/hand/v2/view", {**payload(), "ops": [None]}),
            ("iterations_string", "/api/hand/v2/advice", {**payload(), "iterations": "bad"}),
            ("fractional_raise", "/api/hand/v2/view", {
                **payload(), "ops": [{"op": "action", "seat_id": 3, "type": "raise", "to": 400.7}],
            }),
        ]
        report["invalid_inputs"] = [
            {"case": name, "expected": "Reject invalid input with 4xx; never truncate chip amounts.",
             "request_result": request(client, path, body)}
            for name, path, body in cases
        ]
        report["statistics_storage_calls"] = {"load": load.call_count, "save": save.call_count}

    destination = Path(__file__).with_suffix(".json")
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Evidence written: {destination}")
    print(json.dumps({
        "short_tables": [{"count": row["count"], "initial": row["initial"].get("status"),
                          "action": row["action"].get("status"), "advice": row["advice"].get("status")}
                         for row in report["short_tables"]],
        "empty_sb_recommendation": report["empty_small_blind"]["recommendation"],
        "icm_present": report["final_table_icm"]["icm_present"],
        "named_get_stats_calls": report["named_learning"]["get_stats_calls"],
        "invalid_inputs": [{"case": row["case"], "status": row["request_result"].get("status")}
                           for row in report["invalid_inputs"]],
        "statistics_storage_calls": report["statistics_storage_calls"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
