import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from jevlean import experiment, progress


class FrozenOutcomeTests(unittest.TestCase):
    def test_both_runners_verify_read_only_and_regenerate_only_explicitly(self) -> None:
        # Mock only Lean execution; exercise each runner's real matrix normalization.
        for runner in (experiment, progress):
            with self.subTest(runner=runner.__name__), tempfile.TemporaryDirectory() as directory:
                oracle = Path(directory) / "outcomes.json"
                with patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "")):
                    with patch.object(Path, "write_text", side_effect=AssertionError("verification wrote a file")):
                        with self.assertRaisesRegex(RuntimeError, "Cannot read frozen outcomes"):
                            runner.check_lean(outcome_path=oracle)
                    self.assertFalse(oracle.exists())

                    actual = runner.check_lean(outcome_path=oracle, regenerate=True)
                    original = oracle.read_bytes()
                    self.assertEqual(json.loads(original), actual)
                    with patch.object(Path, "write_text", side_effect=AssertionError("verification wrote a file")):
                        self.assertEqual(runner.check_lean(outcome_path=oracle), actual)
                    self.assertEqual(oracle.read_bytes(), original)

                    # Flip one fixture outcome while preserving aggregate success.
                    drifted = json.loads(original)
                    if runner is experiment:
                        values = next(iter(drifted["cases"].values()))
                        values[next(iter(values))] = False
                    else:
                        values = next(iter(drifted["progress"].values()))
                        next(iter(values.values()))["immediate_close"] = False
                    oracle.write_text(json.dumps(drifted), encoding="utf-8")
                    frozen = oracle.read_bytes()
                    with patch.object(Path, "write_text", side_effect=AssertionError("verification wrote a file")):
                        with self.assertRaisesRegex(RuntimeError, "Frozen Lean outcomes mismatch") as caught:
                            runner.check_lean(outcome_path=oracle)
                    self.assertIn(f"python3 -m {runner.__name__} check-lean --regenerate", str(caught.exception))
                    self.assertEqual(oracle.read_bytes(), frozen)

                    runner.check_lean(outcome_path=oracle, regenerate=True)
                    self.assertEqual(oracle.read_bytes(), original)

                    oracle.write_text("invalid JSON", encoding="utf-8")
                    with patch.object(Path, "write_text", side_effect=AssertionError("verification wrote a file")):
                        with self.assertRaisesRegex(RuntimeError, "Cannot read frozen outcomes"):
                            runner.check_lean(outcome_path=oracle)
                    self.assertEqual(oracle.read_text(), "invalid JSON")
                    runner.check_lean(outcome_path=oracle, regenerate=True)
                    self.assertEqual(oracle.read_bytes(), original)

    def test_cli_requires_explicit_regeneration_flag(self) -> None:
        for runner, result in ((experiment, {"cases": {}}), (progress, {"progress": {}})):
            for regenerate in (False, True):
                with self.subTest(runner=runner.__name__, regenerate=regenerate):
                    argv = [runner.__name__, "check-lean"] + (["--regenerate"] if regenerate else [])
                    with patch("sys.argv", argv), patch.object(runner, "check_lean", return_value=result) as check:
                        with contextlib.redirect_stdout(io.StringIO()):
                            runner.main()
                    self.assertEqual(check.call_args.kwargs["regenerate"], regenerate)


if __name__ == "__main__":
    unittest.main()
