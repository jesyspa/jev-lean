import json
import socket
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from jevlean.model_broker import BrokerServer, MAX_FRAME_BYTES, ModelBroker, PROTOCOL, validate_frame

REQUEST = {"focused_goal": "⊢ P", "pending_sibling_goals": [], "path": [], "actions": [{"id": "A1", "tactic": "assumption"}]}
STATE = {"focused_goal": "⊢ P", "canonical_state": "P"}


def call(address, frame):
    with socket.create_connection(address, timeout=1) as connection:
        connection.sendall(json.dumps(frame).encode() + b"\n")
        return json.loads(connection.makefile("rb").readline())


class ModelBrokerTests(unittest.TestCase):
    def setUp(self):
        self.rank = Mock()
        self.rank.evaluate.return_value = {"model": "rank-model", "answers": {"ranking": {"type": "choice", "choice": "A1", "probabilities": {"A1": 1}}}}
        self.helper = Mock()
        self.helper.generate.return_value = ([{"proposition": "P", "rationale": "useful"}], {"resolved_model": "helper-model", "usage": {"total_tokens": 3}})
        self.server = BrokerServer(("127.0.0.1", 0), ModelBroker(self.rank, self.helper))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def test_health_is_a_readiness_and_protocol_check(self):
        self.assertEqual(call(self.server.server_address, {"operation": "health"}), {"ok": True, "protocol": PROTOCOL, "capabilities": {"rank": True, "helpers": True}})
        with self.assertRaises(ValueError): validate_frame({"operation": "health", "extra": True})

    def test_helper_route_is_structured_and_provider_failure_is_nonfatal(self):
        frame = {"operation": "helpers", "state": STATE, "max_proposals": 1, "deadline_ms": 1000}
        reply = call(self.server.server_address, frame)
        self.assertEqual(reply["proposals"], [{"proposition": "P", "rationale": "useful"}])
        self.assertEqual(reply["resolved_model"], "helper-model")
        self.helper.generate.side_effect = RuntimeError("provider failed")
        self.assertEqual(call(self.server.server_address, frame)["proposals"], [])
        self.assertTrue(call(self.server.server_address, {"operation": "rank", "request": REQUEST, "deadline_ms": 1000})["ok"])

    def test_outgoing_provider_trace_cannot_exceed_frame_limit(self):
        self.helper.generate.return_value = (
            [{"proposition": "P", "rationale": "useful"}],
            {"usage": {"provider_detail": "x" * MAX_FRAME_BYTES}},
        )
        frame = {"operation": "helpers", "state": STATE, "max_proposals": 1, "deadline_ms": 1000}
        with socket.create_connection(self.server.server_address, timeout=1) as connection:
            connection.sendall(json.dumps(frame).encode() + b"\n")
            raw = connection.makefile("rb").readline(MAX_FRAME_BYTES + 1)
        self.assertLessEqual(len(raw), MAX_FRAME_BYTES)
        self.assertEqual(json.loads(raw), {"ok": False, "error": "response frame exceeds limit"})

    def test_concurrent_clients_share_the_service_without_cross_route_failure(self):
        rank = {"operation": "rank", "request": REQUEST, "deadline_ms": 1000}
        helper = {"operation": "helpers", "state": STATE, "max_proposals": 1, "deadline_ms": 1000}
        with ThreadPoolExecutor(max_workers=12) as pool:
            replies = list(pool.map(lambda frame: call(self.server.server_address, frame), [rank, helper] * 12))
        self.assertTrue(all(reply["ok"] for reply in replies))
        self.assertEqual(sum("ranking" in reply for reply in replies), 12)

    def test_second_service_cannot_attach_to_or_replace_the_first(self):
        with self.assertRaises(OSError):
            BrokerServer(self.server.server_address, ModelBroker())


if __name__ == "__main__": unittest.main()
