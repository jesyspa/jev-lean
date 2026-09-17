from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import MODEL

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "benchmark.json"
DEFAULT_TRACE = ROOT / "artifacts" / "jev-1.13.0-trace.jsonl"
DEFAULT_OUTCOMES = ROOT / "artifacts" / "lean-outcomes.json"
ENDPOINT = "https://api.typesafe.ai/v1/systemone"


def load_benchmark() -> dict[str, Any]:
    with DATA_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


class TypeSafeError(RuntimeError):
    pass


class TypeSafeClient:
    """Minimal standard-library client; the API key is never retained in a trace."""

    def __init__(self, api_key: str, retries: int = 4, timeout: float = 60.0) -> None:
        if not api_key:
            raise TypeSafeError("TYPESAFE_API_KEY is not set")
        self._api_key = api_key
        self._retries = retries
        self._timeout = timeout

    def evaluate(self, payload: dict[str, Any]) -> tuple[str, dict[str, Any], float]:
        body = canonical_json(payload)
        request = urllib.request.Request(
            ENDPOINT,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": "jev-lean-prototype/0.1",
            },
        )
        started = time.monotonic()
        for attempt in range(self._retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    raw = response.read().decode("utf-8")
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise TypeSafeError("TypeSafe response is not an object")
                return raw, parsed, time.monotonic() - started
            except urllib.error.HTTPError as error:
                if error.code not in (429, 529) or attempt == self._retries:
                    safe_detail = error.read().decode("utf-8", errors="replace")[:500]
                    raise TypeSafeError(f"TypeSafe HTTP {error.code}: {safe_detail}") from error
                retry_after = error.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else 2**attempt
                time.sleep(min(delay, 30.0))
            except urllib.error.URLError as error:
                if attempt == self._retries:
                    raise TypeSafeError(f"TypeSafe request failed: {error.reason}") from error
                time.sleep(2**attempt)
        raise AssertionError("retry loop must return or raise")


def action_question(actions: list[dict[str, str]]) -> dict[str, Any]:
    criteria = {
        action["id"]: {"action": action["tactic"], "when_useful": action["description"]}
        for action in actions
    }
    criteria["llm_tactic_fallback"] = {
        "action": "Ask the fallback LLM for a direct proof action.",
        "when_useful": "Choose only when no listed Lean action is likely to close the goal, but a direct tactic or term could.",
    }
    criteria["llm_helper_fallback"] = {
        "action": "Ask the fallback LLM for an auxiliary declaration and revised proof.",
        "when_useful": "Choose only when proof structure must change through a helper lemma or definition.",
    }
    return {
        "type": "choice",
        "instructions": {
            "question": "Which one action is most likely to close the displayed Lean goal as written?",
            "constraints": [
                "Choose by Lean 4 tactic semantics, not by general popularity.",
                "The action is inserted immediately after `by`.",
                "Use a fallback only if no listed action is likely to close the goal.",
            ],
        },
        "criteria": criteria,
    }


def route_question() -> dict[str, Any]:
    return {
        "type": "choice",
        "instructions": "Which controller route is appropriate for this Lean proof state?",
        "criteria": {
            "listed_action": "At least one action in the standard catalog is likely to close the goal directly.",
            "llm_tactic_fallback": "No listed action is likely, but an LLM should propose a direct tactic or proof term without adding declarations.",
            "llm_helper_fallback": "The proof likely needs an auxiliary lemma, generalized statement, invariant, or definition before direct tactics can work.",
        },
    }


def lemma_question(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "choice",
        "instructions": {
            "question": "Which bounded candidate lemma is most directly useful for closing the displayed Lean goal?",
            "constraint": "Select by the stated signatures. Do not invent an unlisted lemma.",
        },
        "criteria": {candidate["id"]: candidate["signature"] for candidate in case["candidates"]},
    }


def make_payload(kind: str, case: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    if kind == "tactic":
        state = {
            "language": "Lean 4 with Mathlib",
            "proof_state": case["state"],
            "catalog_policy": "The controller has enumerated all actions in the Choice options. Lean will verify the selected action.",
        }
        question = action_question(data["actions"])
    elif kind == "routing":
        state = {
            "language": "Lean 4 with Mathlib",
            "proof_state_or_obstruction": case["state"],
            "standard_catalog": [action["tactic"] for action in data["actions"]],
        }
        question = route_question()
    elif kind == "lemma":
        state = {"language": "Lean 4 with Mathlib", "proof_state": case["state"]}
        question = lemma_question(case)
    else:
        raise ValueError(f"unknown experiment kind: {kind}")
    return {"state": state, "model": MODEL, "questions": {"decision": question}}


def validate_choice_response(response: dict[str, Any], payload: dict[str, Any]) -> None:
    if not isinstance(response.get("model"), str):
        raise TypeSafeError("response has no resolved model")
    answer = response.get("answers", {}).get("decision")
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise TypeSafeError("response has no Choice answer for decision")
    options = set(payload["questions"]["decision"]["criteria"])
    probabilities = answer.get("probabilities")
    if answer.get("choice") not in options or not isinstance(probabilities, dict):
        raise TypeSafeError("Choice answer does not match request options")
    if set(probabilities) != options:
        raise TypeSafeError("Choice probability keys do not match request options")
    if abs(sum(float(value) for value in probabilities.values()) - 1.0) > 0.01:
        raise TypeSafeError("Choice probabilities do not sum to one")


def run_live(trace_path: Path = DEFAULT_TRACE) -> list[dict[str, Any]]:
    data = load_benchmark()
    client = TypeSafeClient(os.environ.get("TYPESAFE_API_KEY", ""))
    records: list[dict[str, Any]] = []
    groups = (
        ("tactic", data["tactic_cases"]),
        ("routing", data["routing_cases"]),
        ("lemma", data["lemma_cases"]),
    )
    for kind, cases in groups:
        for case in cases:
            payload = make_payload(kind, case, data)
            raw, response, latency = client.evaluate(payload)
            validate_choice_response(response, payload)
            records.append(
                {
                    "schema_version": 1,
                    "case_id": case["id"],
                    "experiment": kind,
                    "requested_model": MODEL,
                    "resolved_model": response["model"],
                    "request_sha256": payload_hash(payload),
                    "public_payload_source": str(DATA_PATH.relative_to(ROOT)),
                    "latency_seconds": round(latency, 6),
                    "raw_response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                    "raw_response": raw,
                }
            )
            print(f"{kind:7} {case['id']}: {response['answers']['decision']['choice']}")
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return records


def _lean_path() -> str:
    paths = [ROOT / ".lake" / "build" / "lib" / "lean"]
    paths.extend(
        package / ".lake" / "build" / "lib" / "lean"
        for package in sorted((ROOT / ".lake" / "packages").iterdir())
    )
    return os.pathsep.join(str(path) for path in paths)


def check_lean(outcome_path: Path = DEFAULT_OUTCOMES, timeout: float = 600.0) -> dict[str, Any]:
    """Check the full action matrix in one Lean process to limit memory pressure."""
    data = load_benchmark()
    markers: list[tuple[str, str]] = []
    chunks = [
        "import JevLean\n",
        "set_option linter.unreachableTactic false\nset_option linter.unusedTactic false\n",
    ]
    for case in data["tactic_cases"]:
        for action in data["actions"]:
            marker = f"JEV_FAIL::{case['id']}::{action['id']}"
            markers.append((case["id"], action["id"]))
            chunks.append(
                f"{case['declaration']}\n"
                f"  first\n"
                f"  | (solve | {action['tactic']})\n"
                f"  | (trace \"{marker}\"; sorry)\n"
            )
    with tempfile.NamedTemporaryFile("w", suffix=".lean", encoding="utf-8", delete=False) as handle:
        handle.write("\n".join(chunks))
        path = Path(handle.name)
    environment = os.environ.copy()
    environment["LEAN_PATH"] = _lean_path()
    try:
        result = subprocess.run(
            ["lean", str(path)],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
    finally:
        path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError("Lean matrix check failed:\n" + result.stdout[-4000:])
    failures = set(re.findall(r"JEV_FAIL::([A-Za-z0-9_]+)::([A-Za-z0-9_]+)", result.stdout))
    outcomes: dict[str, dict[str, bool]] = {case["id"]: {} for case in data["tactic_cases"]}
    for case_id, action_id in markers:
        outcomes[case_id][action_id] = (case_id, action_id) not in failures
    normalized = {
        "schema_version": 1,
        "lean_toolchain": (ROOT / "lean-toolchain").read_text().strip(),
        "cases": {
            case_id: {action_id: values[action_id] for action_id in sorted(values)}
            for case_id, values in sorted(outcomes.items())
        },
    }
    missing = [case_id for case_id, values in normalized["cases"].items() if not any(values.values())]
    if missing:
        raise RuntimeError(f"catalog has no verified successful action for: {', '.join(missing)}")
    outcome_path.parent.mkdir(parents=True, exist_ok=True)
    outcome_path.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return normalized


def load_trace(path: Path = DEFAULT_TRACE) -> list[dict[str, Any]]:
    records = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            raw = record["raw_response"]
            if hashlib.sha256(raw.encode()).hexdigest() != record["raw_response_sha256"]:
                raise ValueError(f"raw response hash mismatch for {record.get('case_id')}")
            response = json.loads(raw)
            record["response"] = response
            records.append(record)
    return records


def verify_trace(records: list[dict[str, Any]], data: dict[str, Any]) -> None:
    expected: dict[tuple[str, str], dict[str, Any]] = {}
    for kind, key in (("tactic", "tactic_cases"), ("routing", "routing_cases"), ("lemma", "lemma_cases")):
        for case in data[key]:
            expected[(kind, case["id"])] = make_payload(kind, case, data)
    if {(record["experiment"], record["case_id"]) for record in records} != set(expected):
        raise ValueError("trace cases do not match the benchmark")
    for record in records:
        key = (record["experiment"], record["case_id"])
        if record["request_sha256"] != payload_hash(expected[key]):
            raise ValueError(f"request hash mismatch for {record['case_id']}")
        validate_choice_response(record["response"], expected[key])
        if record["requested_model"] != MODEL or record["resolved_model"] != record["response"]["model"]:
            raise ValueError(f"model metadata mismatch for {record['case_id']}")


def token_words(text: str) -> set[str]:
    return {word.lower() for word in re.findall(r"[A-Za-z][A-Za-z0-9_.]+", text)}


def overlap_baseline(case: dict[str, Any]) -> str:
    state_words = token_words(case["state"])
    scored = []
    for index, candidate in enumerate(case["candidates"]):
        words = token_words(candidate["id"] + " " + candidate["signature"])
        scored.append((len(state_words & words), -index, candidate["id"]))
    return max(scored)[2]


def compute_metrics(
    trace_path: Path = DEFAULT_TRACE, outcome_path: Path = DEFAULT_OUTCOMES
) -> dict[str, Any]:
    data = load_benchmark()
    outcomes = json.loads(outcome_path.read_text(encoding="utf-8"))["cases"]
    records = load_trace(trace_path)
    verify_trace(records, data)
    choices = {
        (record["experiment"], record["case_id"]): record["response"]["answers"]["decision"]["choice"]
        for record in records
    }
    rng = random.Random(20260917)
    action_ids = [action["id"] for action in data["actions"]]
    tactic_rows = []
    for case in data["tactic_cases"]:
        verified = outcomes[case["id"]]
        selected = choices[("tactic", case["id"])]
        random_pick = rng.choice(action_ids)
        tactic_rows.append(
            {
                "case_id": case["id"],
                "jev_choice": selected,
                "jev_success": bool(verified.get(selected, False)),
                "fixed_first_success": bool(verified[action_ids[0]]),
                "seeded_random_choice": random_pick,
                "seeded_random_success": bool(verified[random_pick]),
                "random_expected_success": sum(verified.values()) / len(action_ids),
                "aesop_success": bool(verified["aesop"]),
                "successful_actions": [name for name, value in verified.items() if value],
            }
        )
    routing_rows = []
    for case in data["routing_cases"]:
        selected = choices[("routing", case["id"])]
        routing_rows.append(
            {"case_id": case["id"], "jev_choice": selected, "gold": case["gold_route"], "correct": selected == case["gold_route"]}
        )
    lemma_rows = []
    for case in data["lemma_cases"]:
        selected = choices[("lemma", case["id"])]
        random_pick = rng.choice([candidate["id"] for candidate in case["candidates"]])
        lemma_rows.append(
            {
                "case_id": case["id"],
                "jev_choice": selected,
                "gold": case["gold"],
                "correct": selected == case["gold"],
                "first_correct": case["candidates"][0]["id"] == case["gold"],
                "seeded_random_correct": random_pick == case["gold"],
                "overlap_choice": overlap_baseline(case),
                "overlap_correct": overlap_baseline(case) == case["gold"],
            }
        )
    total_tokens = sum(record["response"].get("usage", {}).get("input_tokens", 0) for record in records)
    output_tokens = sum(record["response"].get("usage", {}).get("output_tokens", 0) for record in records)
    helper_cases = [row for row in routing_rows if row["gold"] == "llm_helper_fallback"]
    direct_fallback_cases = [row for row in routing_rows if row["gold"] == "llm_tactic_fallback"]
    listed_cases = [row for row in routing_rows if row["gold"] == "listed_action"]
    metrics = {
        "model_versions": sorted({record["resolved_model"] for record in records}),
        "requests": len(records),
        "input_tokens": total_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd_at_recorded_price": round(total_tokens * 0.042 / 1_000_000, 9),
        "latency_seconds_total": round(sum(record["latency_seconds"] for record in records), 6),
        "tactic": {
            "n": len(tactic_rows),
            "jev_top1": sum(row["jev_success"] for row in tactic_rows),
            "fixed_first_top1": sum(row["fixed_first_success"] for row in tactic_rows),
            "seeded_random_top1": sum(row["seeded_random_success"] for row in tactic_rows),
            "random_expected_top1": sum(row["random_expected_success"] for row in tactic_rows),
            "aesop_top1": sum(row["aesop_success"] for row in tactic_rows),
            "oracle_catalog": sum(bool(row["successful_actions"]) for row in tactic_rows),
            "rows": tactic_rows,
        },
        "routing": {
            "n": len(routing_rows),
            "jev_correct": sum(row["correct"] for row in routing_rows),
            "majority_listed_correct": sum(row["gold"] == "listed_action" for row in routing_rows),
            "helper_correct": sum(row["correct"] for row in helper_cases),
            "helper_n": len(helper_cases),
            "direct_fallback_correct": sum(row["correct"] for row in direct_fallback_cases),
            "direct_fallback_n": len(direct_fallback_cases),
            "listed_correct": sum(row["correct"] for row in listed_cases),
            "listed_n": len(listed_cases),
            "rows": routing_rows,
        },
        "lemma": {
            "n": len(lemma_rows),
            "jev_top1": sum(row["correct"] for row in lemma_rows),
            "first_top1": sum(row["first_correct"] for row in lemma_rows),
            "seeded_random_top1": sum(row["seeded_random_correct"] for row in lemma_rows),
            "overlap_top1": sum(row["overlap_correct"] for row in lemma_rows),
            "rows": lemma_rows,
        },
    }
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check-lean", help="verify every tactic transition with one Lean process")
    check_parser.add_argument("--timeout", type=float, default=600.0)
    subparsers.add_parser("live", help="run the pinned Jev experiment and replace the trace")
    subparsers.add_parser("metrics", help="print metrics from committed trace and Lean outcomes")
    args = parser.parse_args()
    if args.command == "check-lean":
        result = check_lean(timeout=args.timeout)
        print(f"verified {len(result['cases'])} tactic cases")
    elif args.command == "live":
        records = run_live()
        print(f"wrote {len(records)} safe trace records to {DEFAULT_TRACE.relative_to(ROOT)}")
    elif args.command == "metrics":
        print(json.dumps(compute_metrics(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
