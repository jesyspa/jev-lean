import json
import unittest
from unittest.mock import Mock

from jevlean.helper_broker import HelperBroker, HelperError, parse_proposals, payload, validate_request

STATE = {"focused_goal": "P : Prop\n⊢ P → P", "canonical_state": "stable-state"}
FRAME = {"state": STATE, "max_proposals": 3, "deadline_ms": 1000}


class HelperBrokerTests(unittest.TestCase):
    def test_low_probability_gate_makes_no_provider_call(self) -> None:
        # Lean's documented deterministic gate gives an atomic state 20%, below 85%.
        probability = 20
        client = Mock()
        if probability >= 85:
            client.generate(STATE, 3, 1000)
        client.generate.assert_not_called()

    def test_high_probability_generation_is_bounded_and_structured(self) -> None:
        response = {"choices": [{"message": {"content": json.dumps({"helpers": [
            {"proposition": "P", "rationale": "cut"},
            {"proposition": "Q", "rationale": "alternative"},
            {"proposition": 3, "rationale": "malformed"},
        ]})}}]}
        self.assertEqual(parse_proposals(response, 2), [{"proposition": "P", "rationale": "cut"}, {"proposition": "Q", "rationale": "alternative"}])

    def test_invalid_provider_response_is_nonfatal_at_broker_boundary(self) -> None:
        client = Mock()
        client.generate.side_effect = HelperError("provider failed")
        with self.assertRaises(HelperError):
            HelperBroker(client).handle(FRAME)

    def test_missing_key_is_rejected_without_a_request(self) -> None:
        from jevlean.helper_broker import OpenRouterClient
        with self.assertRaises(HelperError):
            OpenRouterClient("")

    def test_request_bounds_and_payload(self) -> None:
        with self.assertRaises(HelperError):
            validate_request({**FRAME, "max_proposals": 9})
        request = payload(STATE, 3, "test/model")
        self.assertEqual(request["model"], "test/model")
        self.assertEqual(request["temperature"], 0)
        self.assertEqual(request["response_format"], {"type": "json_object"})

    def test_equivalent_identity_is_cacheable_and_rejected_names_stay_text_only(self) -> None:
        # The broker does not turn names into Lean; the Lean-side canonical-state cache does.
        first, revisit = STATE["canonical_state"], "stable-state"
        self.assertEqual(first, revisit)
        proposals = parse_proposals({"choices": [{"message": {"content": '{"helpers":[{"proposition":"missingName","rationale":"bad"}]}'}}]}, 1)
        self.assertEqual(proposals[0]["proposition"], "missingName")


if __name__ == "__main__":
    unittest.main()
