from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import MODEL
from .experiment import TypeSafeClient, canonical_json, payload_hash

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "progress-benchmark.json"
INDEX_PATH = ROOT / "data" / "library-index.json"
FREEZE_PATH = ROOT / "data" / "progress-prompt-freeze.json"
TRACE_PATH = ROOT / "artifacts" / "progress-jev-1.13.0-trace.jsonl"
OUTCOME_PATH = ROOT / "artifacts" / "progress-lean-outcomes.json"


def load_data() -> dict[str, Any]:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def load_index() -> dict[str, Any]:
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def words(text: str) -> set[str]:
    return {word.lower() for word in re.findall(r"[A-Za-z][A-Za-z0-9_]+", text)}


def retrieve(query: str, limit: int, index: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Return a deterministic bounded ranking from the frozen compact index."""
    declarations = (index or load_index())["declarations"]
    query_words = words(query)
    scored = []
    for position, declaration in enumerate(declarations):
        text_words = words(declaration["name"] + " " + declaration["signature"])
        score = len(query_words & text_words)
        scored.append((-score, position, declaration))
    return [item[2] for item in sorted(scored)[:limit]]


def action_id(prefix: str, value: str) -> str:
    return prefix + "_" + re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()


def generate_actions(case: dict[str, Any], data: dict[str, Any]) -> list[dict[str, str]]:
    actions = [dict(action, source="standard") for action in data["generator"]["standard_actions"]]
    for hypothesis in case.get("local_hypotheses", []):
        for mode in data["generator"]["local_modes"]:
            actions.append(
                {
                    "id": action_id(mode, hypothesis),
                    "tactic": f"{mode} {hypothesis}",
                    "description": f"Generate `{mode} {hypothesis}` from local hypothesis {hypothesis}.",
                    "source": "local",
                }
            )
    for variable in case.get("induction_variables", []):
        actions.extend(
            [
                {
                    "id": action_id("induction", variable),
                    "tactic": f"induction {variable}",
                    "description": f"Structurally induct on local variable {variable}.",
                    "source": "structural",
                },
                {
                    "id": action_id("cases", variable),
                    "tactic": f"cases {variable}",
                    "description": f"Split local variable {variable} into constructors without an induction hypothesis.",
                    "source": "structural",
                },
            ]
        )
    for action in case.get("custom_actions", []):
        actions.append(dict(action, source="structural"))
    query = case.get("retrieval_query", "")
    if query:
        for declaration in retrieve(query, data["generator"]["retrieval_limit"]):
            name = declaration["name"]
            actions.append(
                {
                    "id": action_id("apply", name),
                    "tactic": f"apply {name}",
                    "description": f"Apply retrieved declaration `{name} : {declaration['signature']}`.",
                    "source": "retrieval",
                }
            )
    deduplicated: dict[str, dict[str, str]] = {}
    for action in actions:
        deduplicated.setdefault(action["id"], action)
    return list(deduplicated.values())


def progress_question(case: dict[str, Any], actions: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "type": "choice",
        "instructions": {
            "question": "Which generated Lean action is the best next step toward a proof under the bounded verification protocol?",
            "operational_definition": "A useful next step need not close immediately. Lean must accept it, and either it closes or at least one displayed bounded continuation closes every verified successor goal.",
            "constraints": [
                "Select one listed concrete action; do not invent syntax.",
                "Prefer an action likely to create simpler successor goals with a verified bounded continuation.",
                "Do not assume one-shot aesop succeeds on these deliberately structural cases.",
            ],
        },
        "criteria": {
            action["id"]: {
                "lean_action": action["tactic"],
                "generated_from": action["source"],
                "description": action["description"],
            }
            for action in actions
        },
    }


def lemma_question(case: dict[str, Any], candidates: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "type": "choice",
        "instructions": {
            "question": "Which retrieved Mathlib declaration is most useful for this Lean goal?",
            "operational_definition": "The controller verifies the same explicit argument application for every retrieved declaration; Lean alone decides whether it closes.",
            "constraint": "Choose only from the bounded retrieval result.",
        },
        "criteria": {candidate["name"]: candidate["signature"] for candidate in candidates},
    }


def routing_question() -> dict[str, Any]:
    return {
        "type": "choice",
        "instructions": {
            "question": "Should the bounded controller continue with a generated direct action or route to a structural helper declaration?",
            "operational_definition": "Direct means some displayed action plus bounded continuation should compile. Structural helper means that direct budget is exhausted and a separate generalized or inductive declaration is needed.",
        },
        "criteria": {
            "direct_action": "Execute the ranked generated actions and bounded continuations without adding declarations.",
            "structural_helper": "Request and verify a separate helper declaration, then use it in the headline proof.",
        },
    }


def payload_for(kind: str, case: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    if kind == "progress":
        actions = generate_actions(case, data)
        state = {
            "language": "Lean 4 with pinned Mathlib",
            "proof_state": case["state"],
            "bounded_continuations": [item["tactic"] for item in case["continuations"]],
            "candidate_count": len(actions),
        }
        question = progress_question(case, actions)
    elif kind == "lemma":
        candidates = retrieve(case["retrieval_query"], data["generator"]["retrieval_limit"])
        state = {
            "language": "Lean 4 with pinned Mathlib",
            "proof_state": case["state"],
            "retrieval_query": case["retrieval_query"],
            "retrieval_limit": data["generator"]["retrieval_limit"],
        }
        question = lemma_question(case, candidates)
    elif kind == "routing":
        actions = generate_actions(case, data)
        state = {
            "language": "Lean 4 with pinned Mathlib",
            "proof_state_or_obstruction": case["state"],
            "generated_direct_actions": [action["tactic"] for action in actions],
            "bounded_continuations": [item["tactic"] for item in case["continuations"]],
        }
        question = routing_question()
    else:
        raise ValueError(f"unknown kind {kind}")
    return {"state": state, "model": MODEL, "questions": {"decision": question}}


def all_cases(data: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        *(("progress", case) for case in data["progress_cases"]),
        *(("lemma", case) for case in data["lemma_cases"]),
        *(("routing", case) for case in data["routing_cases"]),
    ]


def validate_response(response: dict[str, Any], payload: dict[str, Any]) -> None:
    answer = response.get("answers", {}).get("decision")
    options = set(payload["questions"]["decision"]["criteria"])
    if response.get("model") != MODEL:
        raise ValueError(f"resolved model is {response.get('model')!r}, expected {MODEL!r}")
    if not isinstance(answer, dict) or answer.get("type") != "choice" or answer.get("choice") not in options:
        raise ValueError("malformed Choice response")
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != options:
        raise ValueError("Choice probabilities do not match options")
    if abs(sum(float(value) for value in probabilities.values()) - 1.0) > 0.011:
        raise ValueError("Choice probabilities do not sum to one")


def freeze() -> dict[str, Any]:
    data = load_data()
    entries = []
    for kind, case in all_cases(data):
        payload = payload_for(kind, case, data)
        entries.append({"experiment": kind, "case_id": case["id"], "request_sha256": payload_hash(payload)})
    manifest = {
        "schema_version": 1,
        "model": MODEL,
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "policy": "Created and committed before the first held-out TypeSafe call; changing any prompt, case, generated candidate, continuation, or retrieval result invalidates replay.",
        "data_sha256": hashlib.sha256(DATA_PATH.read_bytes()).hexdigest(),
        "index_sha256": hashlib.sha256(INDEX_PATH.read_bytes()).hexdigest(),
        "requests": entries,
    }
    FREEZE_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def verify_freeze(data: dict[str, Any]) -> None:
    manifest = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    if manifest["model"] != MODEL:
        raise ValueError("frozen model mismatch")
    if manifest["data_sha256"] != hashlib.sha256(DATA_PATH.read_bytes()).hexdigest():
        raise ValueError("benchmark changed after prompt freeze")
    if manifest["index_sha256"] != hashlib.sha256(INDEX_PATH.read_bytes()).hexdigest():
        raise ValueError("retrieval index changed after prompt freeze")
    expected = {(item["experiment"], item["case_id"]): item["request_sha256"] for item in manifest["requests"]}
    actual = {(kind, case["id"]): payload_hash(payload_for(kind, case, data)) for kind, case in all_cases(data)}
    if expected != actual:
        raise ValueError("generated requests changed after prompt freeze")


def run_live() -> list[dict[str, Any]]:
    data = load_data()
    verify_freeze(data)
    client = TypeSafeClient(os.environ.get("TYPESAFE_API_KEY", ""))
    records = []
    for kind, case in all_cases(data):
        payload = payload_for(kind, case, data)
        raw, response, latency = client.evaluate(payload)
        validate_response(response, payload)
        records.append(
            {
                "schema_version": 2,
                "experiment": kind,
                "case_id": case["id"],
                "split": case["split"],
                "requested_model": MODEL,
                "resolved_model": response["model"],
                "request_sha256": payload_hash(payload),
                "raw_response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "latency_seconds": round(latency, 6),
                "raw_response": raw,
            }
        )
        print(f"{kind:8} {case['id']}: {response['answers']['decision']['choice']}")
    with TRACE_PATH.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return records


def load_trace(data: dict[str, Any]) -> list[dict[str, Any]]:
    verify_freeze(data)
    records = [json.loads(line) for line in TRACE_PATH.read_text(encoding="utf-8").splitlines() if line]
    expected = {(kind, case["id"]) for kind, case in all_cases(data)}
    if {(item["experiment"], item["case_id"]) for item in records} != expected:
        raise ValueError("trace cases do not match frozen benchmark")
    for record in records:
        raw = record["raw_response"]
        if hashlib.sha256(raw.encode()).hexdigest() != record["raw_response_sha256"]:
            raise ValueError(f"response hash mismatch for {record['case_id']}")
        case = next(case for kind, case in all_cases(data) if kind == record["experiment"] and case["id"] == record["case_id"])
        payload = payload_for(record["experiment"], case, data)
        if record["request_sha256"] != payload_hash(payload):
            raise ValueError(f"request hash mismatch for {record['case_id']}")
        response = json.loads(raw)
        validate_response(response, payload)
        record["response"] = response
    return records


def _lean_path() -> str:
    paths = [ROOT / ".lake" / "build" / "lib" / "lean"]
    paths.extend(package / ".lake" / "build" / "lib" / "lean" for package in sorted((ROOT / ".lake" / "packages").iterdir()))
    return os.pathsep.join(str(path) for path in paths)


def _matrix_example(declaration: str, tactic: str, marker: str) -> str:
    return f'{declaration}\n  first\n  | (solve | {tactic})\n  | (trace "{marker}"; sorry)\n'


def _bounded_tactic(action: str, continuations: list[dict[str, str]]) -> str:
    choices = " | ".join(f"({item['tactic']})" for item in continuations)
    return f"{action} <;> first | {choices}"


def _lemma_tactic(name: str, arguments: list[str]) -> str:
    application = name + (" " + " ".join(arguments) if arguments else "")
    return f"exact {application}"


def check_lean(timeout: float = 900.0) -> dict[str, Any]:
    data = load_data()
    chunks = ["import JevLean\n", "set_option linter.unreachableTactic false\nset_option linter.unusedTactic false\n"]
    progress_meta: list[tuple[str, str]] = []
    for case in data["progress_cases"]:
        for action in generate_actions(case, data):
            key = (case["id"], action["id"])
            progress_meta.append(key)
            chunks.append(_matrix_example(case["declaration"], action["tactic"], f"JEV_IMMEDIATE::{key[0]}::{key[1]}"))
            chunks.append(_matrix_example(case["declaration"], _bounded_tactic(action["tactic"], case["continuations"]), f"JEV_BOUNDED::{key[0]}::{key[1]}"))
    lemma_meta: list[tuple[str, str]] = []
    for case in data["lemma_cases"]:
        for candidate in retrieve(case["retrieval_query"], data["generator"]["retrieval_limit"]):
            key = (case["id"], candidate["name"])
            lemma_meta.append(key)
            chunks.append(_matrix_example(case["declaration"], _lemma_tactic(candidate["name"], case["application_arguments"]), f"JEV_LEMMA::{key[0]}::{key[1]}"))
    route_meta: list[tuple[str, str]] = []
    for case in data["routing_cases"]:
        for action in generate_actions(case, data):
            key = (case["id"], action["id"])
            route_meta.append(key)
            chunks.append(_matrix_example(case["declaration"], _bounded_tactic(action["tactic"], case["continuations"]), f"JEV_ROUTE::{key[0]}::{key[1]}"))
        chunks.append(case["helper_patch"] + "\n")
    with tempfile.NamedTemporaryFile("w", suffix=".lean", encoding="utf-8", delete=False) as handle:
        handle.write("\n".join(chunks))
        path = Path(handle.name)
    environment = os.environ.copy()
    environment["LEAN_PATH"] = _lean_path()
    try:
        result = subprocess.run(["lean", str(path)], cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout, check=False)
    finally:
        path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError("Lean verification process failed:\n" + result.stdout[-8000:])
    immediate_fail = set(re.findall(r"JEV_IMMEDIATE::([A-Za-z0-9_]+)::([A-Za-z0-9_]+)", result.stdout))
    bounded_fail = set(re.findall(r"JEV_BOUNDED::([A-Za-z0-9_]+)::([A-Za-z0-9_]+)", result.stdout))
    lemma_fail = set(re.findall(r"JEV_LEMMA::([A-Za-z0-9_]+)::([A-Za-z0-9_.]+)", result.stdout))
    route_fail = set(re.findall(r"JEV_ROUTE::([A-Za-z0-9_]+)::([A-Za-z0-9_]+)", result.stdout))
    progress: dict[str, dict[str, Any]] = {}
    for case_id, candidate_id in progress_meta:
        progress.setdefault(case_id, {})[candidate_id] = {
            "immediate_close": (case_id, candidate_id) not in immediate_fail,
            "bounded_close": (case_id, candidate_id) not in bounded_fail,
        }
    lemma: dict[str, dict[str, bool]] = {}
    for case_id, name in lemma_meta:
        lemma.setdefault(case_id, {})[name] = (case_id, name) not in lemma_fail
    routing: dict[str, dict[str, bool]] = {}
    for case_id, candidate_id in route_meta:
        routing.setdefault(case_id, {})[candidate_id] = (case_id, candidate_id) not in route_fail
    missing = [case["id"] for case in data["progress_cases"] if not any(value["bounded_close"] for value in progress[case["id"]].values())]
    if missing:
        raise RuntimeError("no operationally useful generated action for " + ", ".join(missing))
    result_data = {
        "schema_version": 2,
        "lean_toolchain": (ROOT / "lean-toolchain").read_text().strip(),
        "operational_rule": "useful iff the action closes immediately or action plus one of the frozen bounded continuations closes all successor goals",
        "progress": progress,
        "lemma": lemma,
        "routing": routing,
        "helper_patches_verified": {case["id"]: True for case in data["routing_cases"]},
    }
    OUTCOME_PATH.write_text(json.dumps(result_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result_data


def deterministic_key(action: dict[str, str]) -> tuple[int, str]:
    priorities = {"local": 0, "retrieval": 1, "structural": 2, "standard": 3}
    tactic_bonus = 0 if action["tactic"].startswith(("exact ", "apply ")) else 1
    if action["id"] == "aesop":
        tactic_bonus = 9
    return priorities[action["source"]] * 10 + tactic_bonus, action["id"]


def rank_of(order: list[str], useful: set[str]) -> int | None:
    return next((index + 1 for index, candidate in enumerate(order) if candidate in useful), None)


def compute_metrics() -> dict[str, Any]:
    data = load_data()
    trace = load_trace(data)
    outcomes = json.loads(OUTCOME_PATH.read_text(encoding="utf-8"))
    by_key = {(record["experiment"], record["case_id"]): record for record in trace}
    budget = data["budgets"]["ordered_attempts"]
    rng = random.Random(data["seed"])
    rows = []
    random_solved_total = 0
    random_rank_total = 0.0
    for case in data["progress_cases"]:
        actions = generate_actions(case, data)
        ids = [action["id"] for action in actions]
        verified = outcomes["progress"][case["id"]]
        useful = {candidate for candidate, value in verified.items() if value["bounded_close"]}
        response = by_key[("progress", case["id"])] ["response"]["answers"]["decision"]
        jev_order = sorted(ids, key=lambda item: (-float(response["probabilities"][item]), ids.index(item)))
        deterministic = [action["id"] for action in sorted(actions, key=deterministic_key)]
        aesop_first = ["aesop"] + [item for item in deterministic if item != "aesop"]
        ranks = {"jev": rank_of(jev_order, useful), "deterministic": rank_of(deterministic, useful), "aesop_first": rank_of(aesop_first, useful), "oracle": 1}
        random_solved = 0
        random_ranks = []
        for _ in range(data["budgets"]["random_permutations"]):
            order = rng.sample(ids, len(ids))
            rank = rank_of(order, useful)
            if rank is not None and rank <= budget:
                random_solved += 1
            if rank is not None:
                random_ranks.append(rank)
        random_rate = random_solved / data["budgets"]["random_permutations"]
        random_solved_total += random_rate
        random_rank_total += sum(random_ranks) / len(random_ranks)
        choice = response["choice"]
        rows.append({
            "case_id": case["id"], "split": case["split"], "candidate_count": len(ids),
            "useful_candidates": sorted(useful), "jev_choice": choice,
            "jev_choice_immediate": verified[choice]["immediate_close"],
            "jev_choice_multi_step": verified[choice]["bounded_close"] and not verified[choice]["immediate_close"],
            "aesop_immediate": verified["aesop"]["immediate_close"], "ranks": ranks,
            "random_budget_solve_rate": random_rate,
        })
    heldout = [row for row in rows if row["split"] == "heldout"]
    def solved(policy: str) -> int:
        return sum(row["ranks"][policy] is not None and row["ranks"][policy] <= budget for row in heldout)
    lemma_rows = []
    for case in data["lemma_cases"]:
        candidates = retrieve(case["retrieval_query"], data["generator"]["retrieval_limit"])
        names = [candidate["name"] for candidate in candidates]
        useful = {name for name, success in outcomes["lemma"][case["id"]].items() if success}
        choice = by_key[("lemma", case["id"])] ["response"]["answers"]["decision"]["choice"]
        lemma_rows.append({"case_id": case["id"], "choice": choice, "correct": choice in useful, "retrieval_recall": bool(useful), "first_correct": names[0] in useful, "useful": sorted(useful), "candidates": names})
    route_rows = []
    for case in data["routing_cases"]:
        direct = any(outcomes["routing"][case["id"]].values())
        gold = "direct_action" if direct else "structural_helper"
        choice = by_key[("routing", case["id"])] ["response"]["answers"]["decision"]["choice"]
        route_rows.append({"case_id": case["id"], "operational_route": gold, "choice": choice, "correct": choice == gold, "direct_verified": direct, "helper_verified": outcomes["helper_patches_verified"][case["id"]]})
    input_tokens = sum(record["response"].get("usage", {}).get("input_tokens", 0) for record in trace)
    output_tokens = sum(record["response"].get("usage", {}).get("output_tokens", 0) for record in trace)
    return {
        "model": MODEL,
        "requests": len(trace),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_input_cost_usd_at_0_042_per_million": round(input_tokens * 0.042 / 1_000_000, 9),
        "actual_billed_cost_usd": None,
        "latency_seconds_total": round(sum(record["latency_seconds"] for record in trace), 6),
        "progress": {
            "heldout_n": len(heldout), "attempt_budget": budget,
            "jev_solved": solved("jev"), "aesop_first_solved": solved("aesop_first"),
            "deterministic_solved": solved("deterministic"), "oracle_solved": solved("oracle"),
            "random_expected_solved": sum(row["random_budget_solve_rate"] for row in heldout),
            "jev_top1_useful": sum(row["ranks"]["jev"] == 1 for row in heldout),
            "jev_top1_multi_step": sum(row["jev_choice_multi_step"] for row in heldout),
            "aesop_one_shot_closed": sum(row["aesop_immediate"] for row in heldout),
            "rows": rows,
        },
        "lemma": {"n": len(lemma_rows), "retrieval_recall": sum(row["retrieval_recall"] for row in lemma_rows), "jev_top1": sum(row["correct"] for row in lemma_rows), "retrieval_first_top1": sum(row["first_correct"] for row in lemma_rows), "rows": lemma_rows},
        "routing": {"n": len(route_rows), "jev_correct": sum(row["correct"] for row in route_rows), "helper_n": sum(row["operational_route"] == "structural_helper" for row in route_rows), "helper_correct": sum(row["correct"] and row["operational_route"] == "structural_helper" for row in route_rows), "direct_n": sum(row["operational_route"] == "direct_action" for row in route_rows), "direct_correct": sum(row["correct"] and row["operational_route"] == "direct_action" for row in route_rows), "rows": route_rows},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Reproducible Jev next-step progress benchmark")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("freeze")
    subparsers.add_parser("live")
    checker = subparsers.add_parser("check-lean")
    checker.add_argument("--timeout", type=float, default=900.0)
    subparsers.add_parser("metrics")
    args = parser.parse_args()
    if args.command == "freeze":
        result = freeze()
        print(f"froze {len(result['requests'])} requests")
    elif args.command == "live":
        print(f"wrote {len(run_live())} trace records")
    elif args.command == "check-lean":
        result = check_lean(args.timeout)
        print(f"verified {len(result['progress'])} progress cases")
    else:
        print(json.dumps(compute_metrics(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
