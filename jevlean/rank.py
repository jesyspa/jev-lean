"""Rank a caller-supplied catalogue of concrete Lean actions.

Input and output are JSON objects on standard streams. The ranker only returns supplied
identifiers, so it cannot introduce Lean syntax into the tactic search.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
from typing import Any

from . import MODEL
from .experiment import TypeSafeClient


def fallback(actions: list[dict[str, Any]]) -> list[str]:
    """Return the stable catalogue order when remote ranking is unavailable."""
    return [action["id"] for action in actions]


def validate_request(request: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(request, dict) or not isinstance(request.get("focused_goal"), str):
        raise ValueError("request must contain a string focused_goal")
    pending = request.get("pending_sibling_goals")
    path = request.get("path")
    if not isinstance(pending, list) or not all(isinstance(goal, str) for goal in pending):
        raise ValueError("pending_sibling_goals must be a list of strings")
    if not isinstance(path, list) or not all(isinstance(action, str) for action in path):
        raise ValueError("path must be a list of strings")
    actions = request.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("request must contain nonempty actions")
    ids: list[str] = []
    for action in actions:
        if not isinstance(action, dict) or not isinstance(action.get("id"), str) or not isinstance(action.get("tactic"), str):
            raise ValueError("each action must contain string id and tactic")
        ids.append(action["id"])
    if len(ids) != len(set(ids)):
        raise ValueError("action ids must be unique")
    context = {
        "focused_goal": request["focused_goal"],
        "pending_sibling_goals": pending,
        "path": path,
    }
    return context, actions


def payload(context: dict[str, Any], actions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "model": MODEL,
        "state": {"language": "Lean 4 with Mathlib", **context},
        "questions": {
            "ranking": {
                "type": "choice",
                "instructions": {
                    "question": "Which concrete Lean action is most likely to close every current goal?",
                    "constraints": ["Do not propose Lean syntax or actions outside this catalogue."],
                },
                "criteria": {action["id"]: {"lean_action": action["tactic"]} for action in actions},
            }
        },
    }


def ranking_from_response(response: Any, ids: list[str]) -> list[str]:
    if not isinstance(response, dict) or not isinstance(response.get("model"), str):
        raise ValueError("unexpected model response")
    answer = response.get("answers", {}).get("ranking")
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise ValueError("response does not contain a choice answer")
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != set(ids):
        raise ValueError("response probabilities do not match the actions")
    try:
        weights = {identifier: float(probabilities[identifier]) for identifier in ids}
    except (TypeError, ValueError) as error:
        raise ValueError("response probabilities are not numeric") from error
    if any(not math.isfinite(weight) or weight < 0.0 for weight in weights.values()) or abs(sum(weights.values()) - 1.0) > 0.01:
        raise ValueError("response probabilities are invalid")
    if answer.get("choice") not in ids:
        raise ValueError("response choice does not match the actions")
    return sorted(ids, key=lambda identifier: weights[identifier], reverse=True)


def rank(
    request: dict[str, Any], api_key: str | None = None, timeout_seconds: float = 5.0
) -> tuple[list[str], str]:
    context, actions = validate_request(request)
    ids = fallback(actions)
    override = os.environ.get("JEV_RANK_ORDER")
    if override:
        supplied = override.split(",")
        if set(supplied) == set(ids) and len(supplied) == len(ids):
            return supplied, "override"
    key = os.environ.get("TYPESAFE_API_KEY", "") if api_key is None else api_key
    if not key:
        return ids, "fallback"
    try:
        _, response, _ = TypeSafeClient(key, retries=0, timeout=timeout_seconds).evaluate(payload(context, actions))
        return ranking_from_response(response, ids), "jev"
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
        return ids, "fallback"


def main() -> None:
    parser = argparse.ArgumentParser(description="Rank a fixed catalogue of Lean actions with Jev")
    parser.add_argument("--plain", action="store_true", help="write ranked identifiers one per line")
    parser.add_argument("--timeout-ms", type=int, default=5_000, help="hard ranking deadline")
    args = parser.parse_args()
    try:
        request = json.load(sys.stdin)
        if args.timeout_ms <= 0:
            ranking, source = fallback(validate_request(request)[1]), "fallback"
        else:
            def timed_out(_signum: int, _frame: object) -> None:
                raise TimeoutError("ranking deadline exceeded")

            previous = signal.signal(signal.SIGALRM, timed_out)
            signal.setitimer(signal.ITIMER_REAL, args.timeout_ms / 1_000)
            try:
                ranking, source = rank(request, timeout_seconds=args.timeout_ms / 1_000)
            except TimeoutError:
                ranking, source = fallback(validate_request(request)[1]), "fallback"
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous)
    except (json.JSONDecodeError, ValueError) as error:
        raise SystemExit(f"invalid rank request: {error}")
    if args.plain:
        print("\n".join(ranking))
    else:
        print(json.dumps({"ranking": ranking, "source": source}, ensure_ascii=False))


if __name__ == "__main__":
    main()
