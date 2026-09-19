import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from jevlean import minif2f


class MiniF2FIntegrityTests(unittest.TestCase):
    def test_frozen_pilot_has_complete_fixed_universe(self):
        pilot = minif2f.load_pilot()
        self.assertEqual(len(pilot["accepted_targets"]), 75)
        self.assertEqual(len(pilot["excluded_targets"]), 169)
        self.assertEqual(pilot["upstream_commit"], minif2f.UPSTREAM_COMMIT)
        self.assertEqual(pilot["mathlib_commit"], minif2f.MATHLIB_COMMIT)
        self.assertEqual(pilot["lean_toolchain"], minif2f.LEAN_TOOLCHAIN)
        self.assertEqual(pilot["accepted_targets"][0]["name"], "mathd_numbertheory_483")

    def test_manifest_contains_statements_but_no_docstrings_or_proofs(self):
        pilot = minif2f.load_pilot()
        encoded = json.dumps(pilot["accepted_targets"])
        self.assertNotIn("/--", encoded)
        self.assertNotIn("sorry", encoded)
        self.assertNotIn("norm_num", encoded)
        self.assertEqual(pilot["premise_policy"], "Mathlib declarations only")

    def test_materialization_has_one_target_and_mathlib_premises(self):
        target = minif2f.load_pilot()["accepted_targets"][0]
        source, audit = minif2f.materialize_target(target)
        self.assertIn("import Mathlib", source)
        self.assertIn("import JevLean", source)
        self.assertIn(target["statement"], source)
        self.assertIn("jev_benchmark?", source)
        self.assertNotIn("MiniF2F", source)
        self.assertFalse(audit["upstream_proof_included"])
        self.assertFalse(audit["upstream_docstring_included"])
        self.assertFalse(audit["benchmark_declarations_available_before_proof"])
        self.assertFalse(audit["later_declarations_available_before_proof"])

    def test_replay_omits_search_import_and_uses_only_supplied_proof(self):
        target = minif2f.load_pilot()["accepted_targets"][0]
        source, _ = minif2f.materialize_target(target, search_import="", proof="rfl")
        self.assertNotIn("JevLean", source)
        self.assertTrue(source.endswith(":= by\n  rfl\n"))

    def test_search_and_replay_can_share_a_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            lean = Path("/toolchain/bin/lean")
            with mock.patch.object(minif2f.benchmark4, "_search_environment",
                                   return_value=(lean, "/lean/path")), \
                 mock.patch.dict("os.environ", {"JEV_MODEL_BROKER_PORT": "18765"}):
                search, _ = minif2f._sandboxed_command(Path.cwd(), workspace, "Search.lean")
                replay, _ = minif2f._sandboxed_command(Path.cwd(), workspace, "Replay.lean")
                self.assertIn("JEV_MODEL_BROKER_PORT", search)
                self.assertIn("JEV_MODEL_BROKER_PORT", replay)

    def test_source_extractor_rejects_equation_style_target(self):
        with self.assertRaises((minif2f.MiniF2FError, minif2f.benchmark4.BenchmarkError)):
            minif2f._theorem_headers("theorem example : True\n  | _ => True.intro\n")

    def test_manifest_rejects_opportunistic_target_replacement(self):
        pilot = minif2f.load_pilot()
        altered = dict(pilot)
        altered["excluded_targets"] = pilot["excluded_targets"][:-1]
        with self.assertRaises(minif2f.MiniF2FError):
            minif2f._validate_manifest(altered)


if __name__ == "__main__":
    unittest.main()
