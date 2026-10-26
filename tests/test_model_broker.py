import http.client
import json
import socket
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import Mock

from jevlean.model_broker import (
    BrokerServer, MAX_FRAME_BYTES, ModelBroker, PersistentOpenRouterClient,
    PersistentTypeSafeClient, PROTOCOL, validate_frame,
)

REQUEST = {"focused_goal": "⊢ P", "pending_sibling_goals": [], "path": [], "actions": [{"id": "A1", "tactic": "assumption"}]}
STATE = {"focused_goal": "⊢ P", "canonical_state": "P"}


def call(address, frame):
    with socket.create_connection(address, timeout=1) as connection:
        connection.sendall(json.dumps(frame).encode() + b"\n")
        return json.loads(connection.makefile("rb").readline())


@contextmanager
def running_broker(broker, **limits):
    with BrokerServer(("127.0.0.1", 0), broker, **limits) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield server.server_address
        finally:
            server.shutdown()
            thread.join()


class BrokerResourceLimitTests(unittest.TestCase):
    def test_partial_frame_times_out_with_error_and_connection_closes(self):
        with running_broker(ModelBroker(), frame_read_timeout=0.03) as address:
            with socket.create_connection(address, timeout=1) as connection:
                connection.sendall(b"{")
                with connection.makefile("rb") as stream:
                    self.assertEqual(json.loads(stream.readline()), {
                        "ok": False, "error": "request frame read deadline exceeded",
                    })
                    self.assertEqual(stream.read(1), b"")
            self.assertTrue(call(address, {"operation": "health"})["ok"])

    def test_connections_over_handler_cap_are_rejected_without_provider_call(self):
        entered = threading.Event()
        release = threading.Event()

        def evaluate(request, timeout):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test did not release provider")
            return {"model": "rank-model", "answers": {"ranking": {"type": "choice", "choice": "A1", "probabilities": {"A1": 1}}}}

        rank = Mock()
        rank.evaluate.side_effect = evaluate
        frame = {"operation": "rank", "request": REQUEST, "deadline_ms": 1000}
        with running_broker(ModelBroker(rank_client=rank), max_handlers=1) as address:
            with ThreadPoolExecutor(max_workers=1) as pool:
                active = pool.submit(call, address, frame)
                try:
                    self.assertTrue(entered.wait(1))
                    with socket.create_connection(address, timeout=1) as excess:
                        with excess.makefile("rb") as stream:
                            self.assertEqual(json.loads(stream.readline()), {
                                "ok": False, "error": "broker handler limit reached",
                            })
                            self.assertEqual(stream.read(1), b"")
                    self.assertEqual(rank.evaluate.call_count, 1)
                finally:
                    release.set()
                reply = active.result(timeout=1)
                self.assertTrue(reply["ok"], reply)


class BrokerDeadlineTests(unittest.TestCase):
    def test_helper_deadline_discards_late_success_without_increasing_budget(self):
        frame = {"operation": "helpers", "state": STATE, "max_proposals": 1, "deadline_ms": 10}
        for elapsed, expired in ((0.009, False), (0.010, True), (0.080, True)):
            with self.subTest(elapsed=elapsed):
                now = [0.0]
                helper = Mock()

                def generate(state, maximum, deadline_ms):
                    self.assertEqual((state, maximum, deadline_ms), (STATE, 1, 10))
                    now[0] += elapsed
                    return ([{"proposition": "P", "rationale": "useful"}], {"resolved_model": "helper"})

                helper.generate.side_effect = generate
                reply = ModelBroker(helper_client=helper, clock=lambda: now[0]).handle(frame)
                if expired:
                    self.assertEqual(reply, {"ok": True, "proposals": [], "source": "failed",
                                             "error": "helper deadline exceeded"})
                else:
                    self.assertEqual(reply["source"], "openrouter")
                    self.assertEqual(len(reply["proposals"]), 1)
                helper.generate.assert_called_once()

    def test_rank_deadline_uses_injected_clock(self):
        rank = Mock()
        rank.evaluate.return_value = {}
        clock = Mock(side_effect=[0.0, 0.010])
        broker = ModelBroker(rank_client=rank, clock=clock)
        with self.assertRaisesRegex(TimeoutError, "ranking deadline exceeded"):
            broker.handle({"operation": "rank", "request": REQUEST, "deadline_ms": 10})


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

    def test_http_protocol_failures_preserve_route_error_contracts(self):
        helper_frame = {"operation": "helpers", "state": STATE, "max_proposals": 1, "deadline_ms": 1000}
        rank_frame = {"operation": "rank", "request": REQUEST, "deadline_ms": 1000}
        for error in (http.client.BadStatusLine("not HTTP"), http.client.IncompleteRead(b"partial", 10)):
            with self.subTest(error=type(error).__name__):
                self.helper.generate.side_effect = error
                helper_reply = {"ok": True, "proposals": [], "source": "failed", "error": str(error)}
                self.assertEqual(self.server.broker.handle(helper_frame), helper_reply)
                self.assertEqual(call(self.server.server_address, helper_frame), helper_reply)
                self.rank.evaluate.side_effect = error
                self.assertEqual(call(self.server.server_address, rank_frame), {"ok": False, "error": str(error)})

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

    def test_queued_provider_requests_expire_before_network_io(self):
        rank = PersistentTypeSafeClient("test")
        helper = PersistentOpenRouterClient("test")
        for client, invoke in (
            (rank, lambda: rank.evaluate({}, 0.01)),
            (helper, lambda: helper.generate(STATE, 1, 10)),
        ):
            client.lock.acquire()
            try:
                with self.assertRaises(TimeoutError):
                    invoke()
                self.assertIsNone(client.connection)
            finally:
                client.lock.release()


if __name__ == "__main__": unittest.main()
