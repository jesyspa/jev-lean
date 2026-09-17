import json
import unittest

from jevlean import MODEL
from jevlean.experiment import (
    TypeSafeClient,
    TypeSafeError,
    action_question,
    compute_metrics,
    load_benchmark,
    make_payload,
    overlap_baseline,
    payload_hash,
)


class ExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = load_benchmark()

    def test_model_is_version_pinned(self) -> None:
        self.assertEqual(MODEL, "jev-1.13.0")
        payload = make_payload("tactic", self.data["tactic_cases"][0], self.data)
        self.assertEqual(payload["model"], MODEL)

    def test_catalog_is_broad_and_has_explicit_fallbacks(self) -> None:
        self.assertGreaterEqual(len(self.data["actions"]), 20)
        criteria = action_question(self.data["actions"])["criteria"]
        self.assertIn("llm_tactic_fallback", criteria)
        self.assertIn("llm_helper_fallback", criteria)

    def test_question_ids_do_not_carry_case_labels(self) -> None:
        for kind, key in (("tactic", "tactic_cases"), ("routing", "routing_cases"), ("lemma", "lemma_cases")):
            payload = make_payload(kind, self.data[key][0], self.data)
            self.assertEqual(set(payload["questions"]), {"decision"})

    def test_payload_hash_is_canonical(self) -> None:
        left = {"b": 2, "a": {"x": 1}}
        right = {"a": {"x": 1}, "b": 2}
        self.assertEqual(payload_hash(left), payload_hash(right))

    def test_no_secret_is_in_payload(self) -> None:
        serialized = json.dumps(make_payload("tactic", self.data["tactic_cases"][0], self.data))
        self.assertNotIn("TYPESAFE_API_KEY", serialized)
        self.assertNotIn("Authorization", serialized)

    def test_client_rejects_missing_key(self) -> None:
        with self.assertRaises(TypeSafeError):
            TypeSafeClient("")

    def test_overlap_baseline_returns_a_candidate(self) -> None:
        for case in self.data["lemma_cases"]:
            selected = overlap_baseline(case)
            self.assertIn(selected, {candidate["id"] for candidate in case["candidates"]})

    def test_gold_routes_are_valid(self) -> None:
        valid = {"listed_action", "llm_tactic_fallback", "llm_helper_fallback"}
        for case in self.data["routing_cases"]:
            self.assertIn(case["gold_route"], valid)

    def test_committed_trace_replays(self) -> None:
        metrics = compute_metrics()
        self.assertEqual(metrics["model_versions"], [MODEL])
        self.assertEqual(metrics["requests"], 28)
        self.assertEqual(metrics["tactic"]["oracle_catalog"], 12)


if __name__ == "__main__":
    unittest.main()
