import json
import unittest

from jevlean.calibration import DEFAULT_OUTPUT, check, run, summarize


class CalibrationTests(unittest.TestCase):
    def test_frozen_calibration_replays(self) -> None:
        summary = summarize(run())
        check(summary)
        self.assertEqual(summary["modes"]["accumulated"]["fixtures"], 6)

    def test_rejects_complete_search_failure(self) -> None:
        summary = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
        for row in summary["rows"]:
            row["solved"] = row["replay_success"] = False
        with self.assertRaisesRegex(RuntimeError, "calibration outcome changed"):
            check(summarize(summary["rows"]))

    def test_rejects_swapped_baseline_outcomes(self) -> None:
        summary = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
        for row in summary["rows"]:
            if row["mode"] == "pre_work" and row["fixture"] in ("retrieval", "rewrite"):
                row["solved"] = row["replay_success"] = not row["solved"]
        with self.assertRaisesRegex(RuntimeError, "calibration outcome changed"):
            check(summarize(summary["rows"]))

    def test_rejects_failed_replay(self) -> None:
        summary = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
        summary["rows"][1]["replay_success"] = False
        with self.assertRaisesRegex(RuntimeError, "replay_success"):
            check(summarize(summary["rows"]))

    def test_rejects_changed_fixture_set(self) -> None:
        for mutation in ("missing", "duplicate", "renamed"):
            with self.subTest(mutation=mutation):
                rows = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))["rows"]
                if mutation == "missing":
                    rows.pop()
                elif mutation == "duplicate":
                    rows[-1] = rows[0].copy()
                else:
                    rows[-1]["fixture"] = "unknown"
                with self.assertRaisesRegex(RuntimeError, "fixture set changed"):
                    check(summarize(rows))


if __name__ == "__main__":
    unittest.main()
