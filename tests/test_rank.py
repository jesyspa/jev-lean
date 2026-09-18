import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from jevlean import MODEL
from jevlean.rank import payload, rank, ranking_from_response


REQUEST = {
    "focused_goal": "P Q : Prop\nhP : P\n⊢ P",
    "pending_sibling_goals": ["P Q : Prop\nhQ : Q\n⊢ Q"],
    "path": ["constructor"],
    "actions": [
        {"id": "A01", "tactic": "all_goals exact h"},
        {"id": "A02", "tactic": "all_goals assumption"},
    ],
}


class RankTests(unittest.TestCase):
    def test_missing_credentials_use_catalogue_order(self) -> None:
        ranking, source = rank(REQUEST, api_key="")
        self.assertEqual((ranking, source), (["A01", "A02"], "fallback"))

    @patch("jevlean.rank.TypeSafeClient")
    def test_complete_remote_ranking_is_returned(self, client: object) -> None:
        client.return_value.evaluate.return_value = (
            "{}",
            {
                "model": MODEL,
                "answers": {
                    "ranking": {
                        "type": "choice",
                        "choice": "A02",
                        "probabilities": {"A01": 0.1, "A02": 0.9},
                    }
                },
            },
            0.1,
        )
        self.assertEqual(rank(REQUEST, api_key="key"), (["A02", "A01"], "jev"))

    @patch("jevlean.rank.TypeSafeClient")
    def test_incomplete_remote_ranking_uses_fallback(self, client: object) -> None:
        client.return_value.evaluate.return_value = (
            "{}",
            {
                "model": MODEL,
                "answers": {
                    "ranking": {
                        "type": "choice",
                        "choice": "A01",
                        "probabilities": {"A01": 1.0},
                    }
                },
            },
            0.1,
        )
        self.assertEqual(rank(REQUEST, api_key="key"), (["A01", "A02"], "fallback"))

    def test_explicit_complete_order_is_returned(self) -> None:
        with patch.dict("os.environ", {"JEV_RANK_ORDER": "A02,A01"}, clear=False):
            self.assertEqual(rank(REQUEST, api_key=""), (["A02", "A01"], "override"))

    def test_plain_cli_writes_one_identifier_per_line(self) -> None:
        environment = os.environ.copy()
        environment["JEV_RANK_ORDER"] = "A02,A01"
        result = subprocess.run(
            [sys.executable, "-m", "jevlean.rank", "--plain"],
            input=json.dumps(REQUEST),
            text=True,
            capture_output=True,
            check=True,
            env=environment,
        )
        self.assertEqual(result.stdout.splitlines(), ["A02", "A01"])

    def test_payload_contains_complete_context_and_only_catalogue_actions(self) -> None:
        context = {key: REQUEST[key] for key in ("focused_goal", "pending_sibling_goals", "path")}
        request_payload = payload(context, REQUEST["actions"])
        self.assertEqual(
            request_payload["state"],
            {"language": "Lean 4 with Mathlib", **context},
        )
        criteria = request_payload["questions"]["ranking"]["criteria"]
        self.assertEqual(set(criteria), {"A01", "A02"})

    def test_nonfinite_probabilities_are_rejected(self) -> None:
        response = {
            "model": MODEL,
            "answers": {
                "ranking": {
                    "type": "choice",
                    "choice": "A01",
                    "probabilities": {"A01": float("nan"), "A02": float("nan")},
                }
            },
        }
        with self.assertRaises(ValueError):
            ranking_from_response(response, ["A01", "A02"])

    @patch("jevlean.rank.TypeSafeClient")
    def test_timeout_uses_stable_fallback(self, client: object) -> None:
        client.return_value.evaluate.side_effect = TimeoutError("deadline")
        self.assertEqual(rank(REQUEST, api_key="key"), (["A01", "A02"], "fallback"))

    def test_malformed_context_is_rejected_by_cli(self) -> None:
        malformed = {**REQUEST, "pending_sibling_goals": "not a list"}
        result = subprocess.run(
            [sys.executable, "-m", "jevlean.rank", "--plain"],
            input=json.dumps(malformed),
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
