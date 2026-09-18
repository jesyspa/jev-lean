"""Bounded OpenRouter helper-state broker.

The broker never executes Lean.  It returns only proposition text and rationales;
Lean elaborates, rejects, ranks, and turns accepted propositions into cuts.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import socketserver
import time
from typing import Any
from urllib.parse import urlsplit

from .experiment import canonical_json

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
MAX_FRAME_BYTES = 1_000_000
MAX_PROPOSALS = 8


class HelperError(ValueError):
    pass


def validate_request(value: Any) -> tuple[dict[str, Any], int]:
    if not isinstance(value, dict) or set(value) != {"state", "max_proposals", "deadline_ms"}:
        raise HelperError("request must contain only state, max_proposals, and deadline_ms")
    state, maximum, deadline = value["state"], value["max_proposals"], value["deadline_ms"]
    if not isinstance(state, dict) or not all(isinstance(state.get(k), str) for k in ("focused_goal", "canonical_state")):
        raise HelperError("state must contain string focused_goal and canonical_state")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= MAX_PROPOSALS:
        raise HelperError(f"max_proposals must be an integer between 1 and {MAX_PROPOSALS}")
    if not isinstance(deadline, int) or isinstance(deadline, bool) or not 1 <= deadline <= 60_000:
        raise HelperError("deadline_ms must be an integer between 1 and 60000")
    return state, maximum


def parse_proposals(response: Any, maximum: int) -> list[dict[str, str]]:
    try:
        content = response["choices"][0]["message"]["content"]
        decoded = json.loads(content)
        proposals = decoded["helpers"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise HelperError("OpenRouter response lacks structured helpers") from error
    if not isinstance(proposals, list):
        raise HelperError("helpers must be a list")
    result: list[dict[str, str]] = []
    for proposal in proposals[:maximum]:
        if not isinstance(proposal, dict) or not isinstance(proposal.get("proposition"), str) or not isinstance(proposal.get("rationale"), str):
            continue
        proposition, rationale = proposal["proposition"].strip(), proposal["rationale"].strip()
        if proposition and rationale and len(proposition) <= 2000 and len(rationale) <= 2000:
            result.append({"proposition": proposition, "rationale": rationale})
    return result


def payload(state: dict[str, Any], maximum: int, model: str) -> dict[str, Any]:
    return {"model": model, "temperature": 0, "max_tokens": 1200, "response_format": {"type": "json_object"}, "messages": [
        {"role": "system", "content": "Return JSON only: {\"helpers\":[{\"proposition\":\"Lean Prop expression\",\"rationale\":\"why this cut helps\"}]}. Propose at most the requested number. Do not use unavailable names, proofs, tactics, declarations, or the current goal unchanged."},
        {"role": "user", "content": f"Suggest bounded intermediate helper propositions for this Lean proof state.\n{state['focused_goal']}\nMaximum: {maximum}"},
    ]}


class OpenRouterClient:
    def __init__(self, api_key: str, endpoint: str = ENDPOINT) -> None:
        if not api_key:
            raise HelperError("OPENROUTER_API_KEY is not set")
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.hostname:
            raise HelperError("OpenRouter endpoint must be HTTPS")
        self.api_key, self.host, self.path, self.port = api_key, parsed.hostname, parsed.path, parsed.port

    def generate(self, state: dict[str, Any], maximum: int, deadline_ms: int) -> list[dict[str, str]]:
        model = os.environ.get("JEV_HELPER_MODEL", "openai/gpt-4o-mini")
        connection = http.client.HTTPSConnection(self.host, self.port, timeout=deadline_ms / 1000)
        try:
            connection.request("POST", self.path, canonical_json(payload(state, maximum, model)), {
                "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "User-Agent": "jev-lean-helper/0.1"})
            response = connection.getresponse()
            raw = response.read()
            if not 200 <= response.status < 300:
                raise HelperError(f"OpenRouter HTTP {response.status}")
            return parse_proposals(json.loads(raw), maximum)
        finally:
            connection.close()


class HelperBroker:
    def __init__(self, client: OpenRouterClient) -> None: self.client = client
    def handle(self, frame: Any) -> dict[str, Any]:
        state, maximum = validate_request(frame)
        return {"ok": True, "proposals": self.client.generate(state, maximum, frame["deadline_ms"])}


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            raw = self.rfile.readline(MAX_FRAME_BYTES + 1)
            if not raw or len(raw) > MAX_FRAME_BYTES or not raw.endswith(b"\n"): raise HelperError("request frame exceeds limit")
            reply = self.server.broker.handle(json.loads(raw))  # type: ignore[attr-defined]
        except (HelperError, OSError, TimeoutError, ValueError, json.JSONDecodeError) as error:
            reply = {"ok": False, "error": str(error)[:500]}
        self.wfile.write(canonical_json(reply) + b"\n")


class HelperBrokerServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    def __init__(self, address: tuple[str, int], broker: HelperBroker) -> None:
        super().__init__(address, _Handler); self.broker = broker


def main() -> None:
    """Compatibility entry point; new deployments use the unified model broker."""
    from .model_broker import main as model_broker_main
    model_broker_main()

if __name__ == "__main__": main()
