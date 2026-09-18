import json
import unittest

from jevlean import MODEL
from pathlib import Path

from jevlean.next_step import MAX_OPTIONS, load_data, payload_for, result_paths, verify_freeze


class NextStepBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = load_data()

    def test_exactly_one_hundred_real_transitions(self) -> None:
        cases = self.data["cases"]
        self.assertEqual(self.data["sample_size"], 100)
        self.assertEqual(len(cases), 100)
        self.assertEqual(len({case["id"] for case in cases}), 100)
        self.assertTrue(all(case["path"].startswith("Sipser/") for case in cases))
        self.assertTrue(all(case["pre_state"] and case["action"] for case in cases))

    def test_bounded_shuffled_options_contain_recorded_action(self) -> None:
        for case in self.data["cases"]:
            self.assertLessEqual(len(case["options"]), MAX_OPTIONS)
            self.assertGreaterEqual(len(case["options"]), 2)
            tactics = {option["id"]: option["tactic"] for option in case["options"]}
            self.assertEqual(tactics[case["recorded_option"]], case["action"])
            self.assertNotEqual(
                [option["generation_rank"] for option in case["options"]],
                sorted(option["generation_rank"] for option in case["options"]),
            )

    def test_prompt_asks_for_progress_not_immediate_closure(self) -> None:
        payload = payload_for(self.data["cases"][0])
        self.assertEqual(payload["model"], MODEL)
        instructions = json.dumps(payload["questions"]["decision"]["instructions"])
        self.assertIn("whole proof", instructions)
        self.assertIn("not whether", instructions)
        self.assertNotIn("recorded_option", json.dumps(payload))

    def test_prompt_freeze_reconstructs(self) -> None:
        verify_freeze(self.data)

    def test_result_artifacts_require_an_external_directory(self) -> None:
        paths = result_paths(Path("/tmp/jev-next-step-results"))
        self.assertEqual(paths["trace"].name, "next-step-100-jev-1.13.0-trace.jsonl")
        with self.assertRaises(ValueError):
            result_paths(Path("artifacts"))


if __name__ == "__main__":
    unittest.main()
