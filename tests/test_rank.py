import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from jevlean import MODEL
from jevlean.rank import payload, rank


REQUEST = {
    "state": "h : P\n⊢ P",
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
            {"model": MODEL, "answers": {"ranking": ["A02", "A01"]}},
            0.1,
        )
        self.assertEqual(rank(REQUEST, api_key="key"), (["A02", "A01"], "jev"))

    @patch("jevlean.rank.TypeSafeClient")
    def test_incomplete_remote_ranking_uses_fallback(self, client: object) -> None:
        client.return_value.evaluate.return_value = (
            "{}",
            {"model": MODEL, "answers": {"ranking": ["A01"]}},
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

    def test_payload_only_exposes_catalogue_actions(self) -> None:
        criteria = payload(REQUEST["state"], REQUEST["actions"])["questions"]["ranking"]["criteria"]
        self.assertEqual(set(criteria), {"A01", "A02"})


if __name__ == "__main__":
    unittest.main()
