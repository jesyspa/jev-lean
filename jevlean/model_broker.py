"""Shared credential-holding localhost model broker.

One newline-delimited JSON frame is accepted per TCP connection.  Requests select an
explicit operation: ``health``, ``rank``, or ``helpers``.  The process binds only
127.0.0.1 and never accepts credentials from clients.
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
from .helper_broker import (
    DEFAULT_PORT as _OLD_HELPER_PORT, HelperError, MAX_PROPOSALS, OpenRouterClient,
    parse_proposals, payload as helper_payload, validate_request as validate_helpers,
)
from .rank import fallback, payload as rank_payload, ranking_from_response, validate_request as validate_rank

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_FRAME_BYTES = 1_000_000
PROTOCOL = "jev-model-broker/1"


class BrokerError(ValueError):
    pass


def safe_error(error: BaseException) -> str:
    """Return a bounded provider error without allowing bearer credentials to escape."""
    text = str(error)
    for key in (os.environ.get("TYPESAFE_API_KEY", ""), os.environ.get("OPENROUTER_API_KEY", "")):
        if key: text = text.replace(key, "[redacted]")
    return text[:500]


def _read_frame(handle: Any) -> dict[str, Any]:
    raw = handle.readline(MAX_FRAME_BYTES + 1)
    if not raw:
        raise BrokerError("missing request frame")
    if len(raw) > MAX_FRAME_BYTES or not raw.endswith(b"\n"):
        raise BrokerError("request frame exceeds limit")
    try:
        result = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BrokerError("request frame is not JSON") from error
    if not isinstance(result, dict):
        raise BrokerError("request frame must be an object")
    return result


def validate_frame(frame: Any) -> tuple[str, Any, float | None]:
    if not isinstance(frame, dict) or not isinstance(frame.get("operation"), str):
        raise BrokerError("frame must contain a string operation")
    operation = frame["operation"]
    if operation == "health":
        if set(frame) != {"operation"}:
            raise BrokerError("health frame must contain only operation")
        return operation, None, None
    if operation == "rank":
        if set(frame) != {"operation", "request", "deadline_ms"}:
            raise BrokerError("rank frame must contain only operation, request, and deadline_ms")
        deadline = frame["deadline_ms"]
        if not isinstance(deadline, int) or isinstance(deadline, bool) or not 0 < deadline <= 60_000:
            raise BrokerError("deadline_ms must be an integer between 1 and 60000")
        context, actions = validate_rank(frame["request"])
        return operation, {"context": context, "actions": actions}, deadline / 1000
    if operation == "helpers":
        if set(frame) != {"operation", "state", "max_proposals", "deadline_ms"}:
            raise BrokerError("helpers frame must contain only operation, state, max_proposals, and deadline_ms")
        state, maximum = validate_helpers({key: frame[key] for key in ("state", "max_proposals", "deadline_ms")})
        return operation, {"state": state, "maximum": maximum}, frame["deadline_ms"] / 1000
    raise BrokerError("operation must be health, rank, or helpers")


def trace(response: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    model = response.get("model")
    usage = response.get("usage")
    if isinstance(model, str): result["resolved_model"] = model
    if isinstance(usage, dict): result["usage"] = usage
    return result


class PersistentTypeSafeClient:
    """A locked reusable HTTPS connection for rank requests only."""
    def __init__(self, api_key: str) -> None:
        if not api_key: raise TypeSafeError("rank capability is unavailable")
        endpoint = urlsplit(ENDPOINT)
        if endpoint.scheme != "https" or not endpoint.hostname: raise RuntimeError("TypeSafe endpoint must be HTTPS")
        self.api_key, self.host, self.port, self.path = api_key, endpoint.hostname, endpoint.port, endpoint.path
        self.connection: http.client.HTTPSConnection | None = None
        self.lock = threading.Lock()

    def evaluate(self, request: dict[str, Any], timeout: float) -> dict[str, Any]:
        if timeout <= 0: raise TimeoutError("ranking deadline exceeded")
        with self.lock:
            try:
                if self.connection is None: self.connection = http.client.HTTPSConnection(self.host, self.port, timeout=timeout)
                self.connection.timeout = timeout
                if self.connection.sock is not None: self.connection.sock.settimeout(timeout)
                self.connection.request("POST", self.path, canonical_json(request), {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "User-Agent": "jev-lean-broker/1"})
                response = self.connection.getresponse(); raw = response.read()
                if not 200 <= response.status < 300: raise TypeSafeError(f"TypeSafe HTTP {response.status}")
                parsed = json.loads(raw)
                if not isinstance(parsed, dict): raise TypeSafeError("TypeSafe response is not an object")
                return parsed
            except (OSError, TimeoutError, http.client.HTTPException, json.JSONDecodeError):
                if self.connection is not None: self.connection.close()
                self.connection = None
                raise


class PersistentOpenRouterClient(OpenRouterClient):
    """The helper provider has an independent locked reusable connection."""
    def __init__(self, api_key: str) -> None:
        super().__init__(api_key)
        self.connection: http.client.HTTPSConnection | None = None
        self.lock = threading.Lock()

    def generate(self, state: dict[str, Any], maximum: int, deadline_ms: int) -> tuple[list[dict[str, str]], dict[str, Any]]:
        model = os.environ.get("JEV_HELPER_MODEL", "openai/gpt-4o-mini")
        with self.lock:
            try:
                if self.connection is None: self.connection = http.client.HTTPSConnection(self.host, self.port, timeout=deadline_ms / 1000)
                self.connection.timeout = deadline_ms / 1000
                if self.connection.sock is not None: self.connection.sock.settimeout(deadline_ms / 1000)
                self.connection.request("POST", self.path, canonical_json(helper_payload(state, maximum, model)), {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "User-Agent": "jev-lean-broker/1"})
                response = self.connection.getresponse(); raw = response.read()
                if not 200 <= response.status < 300: raise HelperError(f"OpenRouter HTTP {response.status}")
                decoded = json.loads(raw)
                if not isinstance(decoded, dict): raise HelperError("OpenRouter response is not an object")
                return parse_proposals(decoded, maximum), trace(decoded)
            except (OSError, TimeoutError, http.client.HTTPException, json.JSONDecodeError):
                if self.connection is not None: self.connection.close()
                self.connection = None
                raise


class ModelBroker:
    def __init__(self, rank_client: Any | None = None, helper_client: Any | None = None) -> None:
        self.rank_client = rank_client
        self.helper_client = helper_client

    def handle(self, frame: Any) -> dict[str, Any]:
        operation, request, timeout = validate_frame(frame)
        if operation == "health":
            return {"ok": True, "protocol": PROTOCOL, "capabilities": {"rank": self.rank_client is not None, "helpers": self.helper_client is not None}}
        if operation == "rank":
            if self.rank_client is None: raise BrokerError("rank capability is unavailable")
            actions = request["actions"]; ids = fallback(actions)
            override = os.environ.get("JEV_RANK_ORDER")
            if override:
                supplied = override.split(",")
                if set(supplied) == set(ids) and len(supplied) == len(ids): return {"ok": True, "ranking": supplied, "source": "override"}
            started = time.monotonic()
            response = self.rank_client.evaluate(rank_payload(request["context"], actions), timeout)
            if time.monotonic() - started >= timeout: raise TimeoutError("ranking deadline exceeded")
            return {"ok": True, "ranking": ranking_from_response(response, ids), "source": "jev", **trace(response)}
        if self.helper_client is None:
            return {"ok": True, "proposals": [], "source": "unavailable"}
        try:
            proposals, provider_trace = self.helper_client.generate(request["state"], request["maximum"], int(timeout * 1000))
            return {"ok": True, "proposals": proposals, "source": "openrouter", **provider_trace}
        except (HelperError, OSError, TimeoutError, ValueError, RuntimeError) as error:
            return {"ok": True, "proposals": [], "source": "failed", "error": safe_error(error)}


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try: reply = self.server.broker.handle(_read_frame(self.rfile))  # type: ignore[attr-defined]
        except (BrokerError, TypeSafeError, TimeoutError, ValueError, OSError, RuntimeError) as error: reply = {"ok": False, "error": safe_error(error)}
        self.wfile.write(canonical_json(reply) + b"\n")


class BrokerServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    request_queue_size = 64
    daemon_threads = True
    def __init__(self, address: tuple[str, int], broker: ModelBroker) -> None:
        super().__init__(address, _Handler); self.broker = broker


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST, choices=[DEFAULT_HOST])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535: raise SystemExit("--port must be between 0 and 65535")
    rank_key, helper_key = os.environ.get("TYPESAFE_API_KEY", ""), os.environ.get("OPENROUTER_API_KEY", "")
    broker = ModelBroker(PersistentTypeSafeClient(rank_key) if rank_key else None, PersistentOpenRouterClient(helper_key) if helper_key else None)
    try: server = BrokerServer((args.host, args.port), broker)
    except OSError as error: raise SystemExit(f"jev model broker cannot bind {args.host}:{args.port}: {safe_error(error)}; another service may be running") from error
    with server:
        host, port = server.server_address
        print(f"jev model broker ready {host}:{port} protocol={PROTOCOL} rank={rank_key != ''} helpers={helper_key != ''}", flush=True)
        server.serve_forever()

if __name__ == "__main__": main()
