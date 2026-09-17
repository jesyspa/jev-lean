import json
import os
from pathlib import Path
import unittest

from jevlean.pantograph_spike import PANTOGRAPH_COMMIT, LEAN_VERSION, run_and_write


class PantographFeasibilityTests(unittest.TestCase):
    """Executable checks for the ten bounded feasibility gates.

    A test passes when the gate was genuinely measured.  Unsupported behavior is
    recorded as a `blocker` with evidence instead of being skipped or mocked.
    The artifact's pass/blocker count is the feasibility verdict.
    """

    @classmethod
    def setUpClass(cls):
        output = os.environ.get("PANTOGRAPH_SPIKE_RESULTS")
        cls.result = run_and_write(Path(output) if output else None)

    def assertStatus(self, gate, expected):
        result = self.result["gates"][gate]
        self.assertEqual(result["status"], expected)
        self.assertTrue(result["evidence"])
        if expected == "blocker":
            self.assertIn("message", result["evidence"])

    def test_01_immutable_branching(self):
        self.assertStatus("immutable_branching", "pass")

    def test_02_fixed_goal_auto_resume(self):
        self.assertStatus("fixed_goal_auto_resume", "pass")

    def test_03_root_unresolved_metavariable(self):
        self.assertStatus("root_unresolved_metavariable", "pass")

    def test_04_independent_suggestions(self):
        self.assertStatus("independent_suggestions", "blocker")
        failures = self.result["gates"]["independent_suggestions"]["evidence"]["failures"]
        self.assertEqual(len(failures), 1)
        self.assertTrue(failures[0].startswith("rw? "))

    def test_05_isolated_helper_lineages(self):
        self.assertStatus("isolated_helper_lineages", "pass")

    def test_06_helper_namespace_instance_attribute(self):
        self.assertStatus("helper_namespace_instance_attribute", "pass")

    def test_07_frontier_recovery(self):
        self.assertStatus("frontier_recovery", "pass")

    def test_08_clean_source_replay(self):
        self.assertStatus("clean_source_replay", "pass")

    def test_09_safe_theorem_hole_extraction(self):
        self.assertStatus("safe_theorem_hole_extraction", "pass")

    def test_10_latency_profile(self):
        self.assertStatus("latency_profile", "pass")

    def test_pin_and_toolchain(self):
        self.assertEqual(self.result["pantograph_commit"], PANTOGRAPH_COMMIT)
        self.assertEqual(self.result["lean_version"], LEAN_VERSION)
        self.assertEqual(self.result["summary"]["total"], 10)


if __name__ == "__main__":
    unittest.main()
