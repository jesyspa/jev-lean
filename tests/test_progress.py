import json
import unittest

from jevlean import MODEL
from jevlean.progress import (
    compute_metrics,
    generate_actions,
    load_data,
    payload_for,
    retrieve,
    verify_freeze,
)


class ProgressBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = load_data()

    def test_benchmark_has_frozen_heldout_multi_step_cases(self) -> None:
        heldout = [case for case in self.data["progress_cases"] if case["split"] == "heldout"]
        self.assertGreaterEqual(len(heldout), 15)
        self.assertTrue(all(case["continuations"] for case in self.data["progress_cases"]))

    def test_actions_are_generated_from_local_context_and_structure(self) -> None:
        local_case = next(case for case in self.data["progress_cases"] if case["id"] == "p11_function_ext_arith")
        local_actions = generate_actions(local_case, self.data)
        self.assertIn("exact h", {action["tactic"] for action in local_actions})
        self.assertIn("funext x", {action["tactic"] for action in local_actions})
        induction_case = self.data["progress_cases"][0]
        self.assertIn("induction xs", {action["tactic"] for action in generate_actions(induction_case, self.data)})

    def test_bounded_retrieval_is_deterministic(self) -> None:
        left = retrieve("Function LeftInverse injective", 6)
        right = retrieve("Function LeftInverse injective", 6)
        self.assertEqual(left, right)
        self.assertEqual(left[0]["name"], "Function.LeftInverse.injective")

    def test_payload_uses_pinned_model_and_no_secret(self) -> None:
        case = self.data["progress_cases"][0]
        payload = payload_for("progress", case, self.data)
        self.assertEqual(payload["model"], MODEL)
        serialized = json.dumps(payload)
        self.assertNotIn("TYPESAFE_API_KEY", serialized)
        self.assertNotIn("Authorization", serialized)

    def test_question_targets_progress_not_immediate_closure(self) -> None:
        case = self.data["progress_cases"][0]
        question = payload_for("progress", case, self.data)["questions"]["decision"]
        instructions = json.dumps(question["instructions"])
        self.assertIn("need not close immediately", instructions)
        self.assertIn("bounded continuation", instructions)

    def test_prompt_freeze_reconstructs_every_request(self) -> None:
        verify_freeze(self.data)

    def test_committed_trace_and_metrics_replay(self) -> None:
        metrics = compute_metrics()
        self.assertEqual(metrics["model"], MODEL)
        self.assertEqual(metrics["requests"], 34)
        self.assertEqual(metrics["progress"]["heldout_n"], 15)
        self.assertEqual(metrics["progress"]["oracle_solved"], 15)
        self.assertEqual(metrics["lemma"]["retrieval_recall"], 10)
        self.assertTrue(all(row["helper_verified"] for row in metrics["routing"]["rows"]))


if __name__ == "__main__":
    unittest.main()
