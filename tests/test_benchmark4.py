import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from jevlean import benchmark4


FIXTURE_SOURCE = """import Init
namespace Fixture

def prior : Nat := 3

theorem target (n : Nat := 1) : n = n := by
  exact rfl

theorem later : False := by
  trivial
end Fixture
"""


class Benchmark4IntegrityTests(unittest.TestCase):
    def fixture(self, root: Path):
        path = root / "Mathlib/Fixture.lean"
        path.parent.mkdir(parents=True)
        path.write_text(FIXTURE_SOURCE, encoding="utf-8")
        task = {
            "id": "ld4-fixture",
            "file_path": "Mathlib/Fixture.lean",
            "full_name": "Fixture.target",
            "start": [6, 1],
            "end": [8, 1],
        }
        return task

    def test_frozen_pilot_identity_and_pin(self):
        pilot = benchmark4.load_pilot()
        self.assertEqual(len(pilot["tasks"]), 50)
        self.assertEqual(pilot["mathlib_commit"], benchmark4.MATHLIB_COMMIT)
        self.assertEqual(pilot["lean_toolchain"], benchmark4.LEAN_TOOLCHAIN)
        self.assertEqual(pilot["split_sha256"], benchmark4.TEST_SHA256)
        self.assertEqual(
            pilot["tasks"][0],
            {
                "id": "ld4-00118da52bc445ea",
                "file_path": "Mathlib/AlgebraicGeometry/ProjectiveSpectrum/Topology.lean",
                "full_name": "ProjectiveSpectrum.mem_vanishingIdeal",
                "start": [109, 1],
                "end": [111, 63],
            },
        )

    def test_selection_does_not_copy_traced_proofs(self):
        records = [
            {
                "file_path": "Mathlib/B.lean",
                "full_name": "B.target",
                "start": [1, 1],
                "end": [1, 20],
                "traced_tactics": [{"tactic": "SECRET_PROOF"}],
            },
            {
                "file_path": ".lake/packages/aesop/A.lean",
                "full_name": "A.target",
                "start": [1, 1],
                "end": [1, 20],
                "traced_tactics": [{"tactic": "ANOTHER_SECRET"}],
            },
        ]
        selected = benchmark4.selected_tasks(records, 50)
        self.assertEqual(len(selected), 1)
        self.assertNotIn("traced_tactics", selected[0])
        self.assertNotIn("SECRET", json.dumps(selected))

    def test_materialization_ends_at_sanitized_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = self.fixture(root)
            source, audit = benchmark4.materialize_task(task, root)
        self.assertIn("import JevLean", source)
        self.assertIn("theorem target (n : Nat := 1) : n = n := by", source)
        self.assertIn("jev_benchmark?", source)
        self.assertNotIn("exact rfl", source)
        self.assertNotIn("theorem later", source)
        self.assertNotIn("end Fixture", source)
        self.assertFalse(audit["original_proof_included"])
        self.assertFalse(audit["original_suffix_included"])
        self.assertFalse(audit["target_declaration_available_before_proof"])

    def test_fresh_replay_contains_generated_proof_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = self.fixture(root)
            source, _ = benchmark4.materialize_task(
                task, root, search_import="", proof="rfl"
            )
        self.assertIn(":= by\n  rfl", source)
        self.assertNotIn("exact rfl", source)
        self.assertNotIn("later", source)

    def test_equation_style_proof_is_replaced(self):
        source = "import Init\ntheorem same : ∀ n : Nat, n = n\n  | n => rfl\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "Mathlib/Equation.lean"
            path.parent.mkdir(parents=True)
            path.write_text(source)
            task = {
                "id": "ld4-equation",
                "file_path": "Mathlib/Equation.lean",
                "full_name": "same",
                "start": [2, 1],
                "end": [3, 14],
            }
            materialized, _ = benchmark4.materialize_task(task, root, search_import="")
        self.assertIn("theorem same : ∀ n : Nat, n = n := by\n  jev_benchmark?", materialized)
        self.assertNotIn("| n => rfl", materialized)

    def test_positions_and_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = self.fixture(root)
            escaped = dict(task, file_path="../secret.lean")
            with self.assertRaises(benchmark4.BenchmarkError):
                benchmark4.materialize_task(escaped, root)
            invalid = dict(task, start=[999, 1])
            with self.assertRaises(benchmark4.BenchmarkError):
                benchmark4.materialize_task(invalid, root)

    def test_environment_audit_reports_exact_toolchain_blocker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "lean-toolchain").write_text("leanprover/lean4:v4.30.0\n")
            with mock.patch.object(benchmark4, "_git_revision", return_value=benchmark4.MATHLIB_COMMIT), \
                    mock.patch.object(benchmark4, "_pilot_sources_clean", return_value=True):
                result = benchmark4.audit_environment(root, root)
        self.assertFalse(result["compatible"])
        self.assertIn("v4.30.0", result["blockers"][0])
        self.assertIn("v4.10.0-rc1", result["blockers"][0])

    def test_credentials_are_removed(self):
        env = benchmark4.credential_free_env(
            {"PATH": "/bin", "TYPESAFE_API_KEY": "secret", "OPENROUTER_API_KEY": "secret"}
        )
        self.assertEqual(env, {"PATH": "/bin"})

    def test_hard_process_timeout_kills_process_group(self):
        result = benchmark4._run_process(
            ["python3", "-c", "import time; time.sleep(10)"],
            Path.cwd(), 0.05, benchmark4.credential_free_env(),
        )
        self.assertTrue(result["timed_out"])
        self.assertLess(result["wall_seconds"], 2)
        self.assertNotEqual(result["returncode"], 0)

    def test_structured_marker(self):
        marker = {
            "status": "solved",
            "proof": "rfl",
            "metrics": {"expanded_nodes": 1},
            "usage": {"jev_calls": 1, "helper_requests": 0, "helper_actions": 0},
        }
        output = "noise\n" + benchmark4.RESULT_MARKER + json.dumps(marker) + "\n"
        self.assertEqual(benchmark4._parse_result(output), marker)


if __name__ == "__main__":
    unittest.main()
