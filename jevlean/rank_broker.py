"""Persistent localhost broker for Jev ranking requests.

The line-delimited JSON protocol accepts one object per connection:
{"request": <rank request>, "deadline_ms": positive integer}
and responds with either {"ok": true, "ranking": [...], "source": ...} or
{"ok": false, "error": <safe message>}. Connections are deliberately closed after
one response so Lean can discard a timed-out request safely.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import socketserver
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from .experiment import ENDPOINT, TypeSafeError, canonical_json
from .rank import fallback, payload, ranking_from_response, validate_request

MAX_FRAME_BYTES = 1_000_000
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class BrokerError(ValueError):
    pass


def _json_line(value: dict[str, Any]) -> bytes:
    return canonical_json(value) + b"\n"


def _read_frame(handle: Any) -> dict[str, Any]:
    raw = handle.readline(MAX_FRAME_BYTES + 1)
    if not raw:
        raise BrokerError("missing request frame")
    if len(raw) > MAX_FRAME_BYTES or not raw.endswith(b"\n"):
        raise BrokerError("request frame exceeds limit")
    try:
        frame = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BrokerError("request frame is not JSON") from error
    if not isinstance(frame, dict):
        raise BrokerError("request frame must be an object")
    return frame


def validate_frame(frame: Any) -> tuple[dict[str, Any], float]:
    if not isinstance(frame, dict) or set(frame) != {"request", "deadline_ms"}:
        raise BrokerError("frame must contain only request and deadline_ms")
    deadline_ms = frame["deadline_ms"]
    if not isinstance(deadline_ms, int) or isinstance(deadline_ms, bool) or not 0 < deadline_ms <= 60_000:
        raise BrokerError("deadline_ms must be an integer between 1 and 60000")
    context, actions = validate_request(frame["request"])
    return {"context": context, "actions": actions}, deadline_ms / 1_000


class PersistentTypeSafeClient:
    """A single reusable HTTPS connection, reset after any transport failure."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise TypeSafeError("TYPESAFE_API_KEY is not set")
        endpoint = urlsplit(ENDPOINT)
        if endpoint.scheme != "https" or not endpoint.hostname:
            raise RuntimeError("TypeSafe endpoint must be HTTPS")
        self._api_key = api_key
        self._host = endpoint.hostname
        self._port = endpoint.port
        self._path = endpoint.path
        self._connection: http.client.HTTPSConnection | None = None
        self._lock = threading.Lock()

    def _reset(self) -> None:
        if self._connection is not None:
            self._connection.close()
        self._connection = None

    def evaluate(self, request_payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        if timeout <= 0:
            raise TimeoutError("ranking deadline exceeded")
        body = canonical_json(request_payload)
        with self._lock:
            try:
                if self._connection is None:
                    self._connection = http.client.HTTPSConnection(self._host, self._port, timeout=timeout)
                self._connection.timeout = timeout
                if self._connection.sock is not None:
                    self._connection.sock.settimeout(timeout)
                self._connection.request("POST", self._path, body=body, headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "jev-lean-prototype/0.1",
                })
                response = self._connection.getresponse()
                raw = response.read()
                if response.status < 200 or response.status >= 300:
                    raise TypeSafeError(f"TypeSafe HTTP {response.status}: {raw.decode(errors='replace')[:500]}")
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise TypeSafeError("TypeSafe response is not an object")
                return parsed
            except (OSError, TimeoutError, http.client.HTTPException, json.JSONDecodeError):
                self._reset()
                raise


class RankBroker:
    def __init__(self, client: PersistentTypeSafeClient) -> None:
        self._client = client

    def rank_frame(self, frame: Any) -> dict[str, Any]:
        validated, timeout = validate_frame(frame)
        actions = validated["actions"]
        ids = fallback(actions)
        override = os.environ.get("JEV_RANK_ORDER")
        if override:
            supplied = override.split(",")
            if set(supplied) == set(ids) and len(supplied) == len(ids):
                return {"ok": True, "ranking": supplied, "source": "override"}
        started = time.monotonic()
        response = self._client.evaluate(payload(validated["context"], actions), timeout)
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("ranking deadline exceeded")
        return {"ok": True, "ranking": ranking_from_response(response, ids), "source": "jev"}


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            reply = self.server.broker.rank_frame(_read_frame(self.rfile))  # type: ignore[attr-defined]
        except (BrokerError, TypeSafeError, TimeoutError, ValueError, OSError, RuntimeError) as error:
            reply = {"ok": False, "error": str(error)[:500]}
        self.wfile.write(_json_line(reply))


class BrokerServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], broker: RankBroker) -> None:
        super().__init__(address, _Handler)
        self.broker = broker


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the persistent localhost Jev ranking broker")
    parser.add_argument("--host", default=DEFAULT_HOST, choices=[DEFAULT_HOST], help="localhost address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP port (1-65535)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if not key:
        raise SystemExit("jev rank broker requires a nonempty TYPESAFE_API_KEY environment variable")
    with BrokerServer((args.host, args.port), RankBroker(PersistentTypeSafeClient(key))) as server:
        print(f"jev rank broker listening on {args.host}:{args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
