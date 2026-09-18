from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import random
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import MODEL
from .experiment import TypeSafeClient, canonical_json, payload_hash
from .progress import load_index, retrieve

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "next-step-100.json"
FREEZE_PATH = ROOT / "data" / "next-step-100-prompt-freeze.json"
RESULT_FILENAMES = {
    "trace": "next-step-100-jev-1.13.0-trace.jsonl",
    "outcomes": "next-step-100-lean-outcomes.json",
    "metrics": "next-step-100-metrics.json",
}
SEED = 20_260_917
SOURCE_REVISION = "57ac584959f03a3e98c4decf04162cd3a1af6b59"
SOURCE_URL = "https://github.com/komiputer/sipser"
MAX_OPTIONS = 10

TACTIC_RE = re.compile(
    r"^(?P<indent> +)(?P<verb>classical|constructor|intro|intros|rintro|exact|apply|refine|rw|simp|simpa|aesop|omega|rfl|cases|induction|obtain|rcases|subst|unfold|change|by_cases|ext|funext|left|right|use|norm_num|linarith|nlinarith|ring|tauto|decide|assumption|contradiction|rename_i|have)\b(?P<rest>.*)$"
)
DECL_RE = re.compile(r"^(?:@\[[^]]+\]\s*)?(?:lemma|theorem|example)\b")
TOP_LEVEL_RE = re.compile(r"^\S")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def result_paths(results_dir: Path) -> dict[str, Path]:
    directory = results_dir.resolve()
    if ROOT == directory or ROOT in directory.parents:
        raise ValueError("next-step result artifacts must be stored outside the repository")
    return {name: directory / filename for name, filename in RESULT_FILENAMES.items()}


def load_data() -> dict[str, Any]:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def source_candidates(source_root: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path in sorted((source_root / "Sipser").rglob("*.lean")):
        if path.stat().st_size > 30_000:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        starts = [index for index, line in enumerate(lines) if DECL_RE.match(line)]
        for start in starts:
            next_top = next(
                (index for index in range(start + 1, len(lines)) if TOP_LEVEL_RE.match(lines[index])),
                len(lines),
            )
            header_end = next((index for index in range(start, next_top) if ":= by" in lines[index]), None)
            if header_end is None:
                continue
            for index in range(header_end + 1, next_top):
                line = lines[index]
                match = TACTIC_RE.match(line)
                if not match:
                    continue
                action = line.strip()
                if len(match.group("indent")) != 2:
                    continue
                if ":= by" in action or action.endswith((";<;>", "<;>", ";", ",", ":=", " with", " using", " ↔", " ∧", " ∨", " ∪", " ∩", " →", " =", " =>")) or "--" in action:
                    continue
                if any(action.count(left) != action.count(right) for left, right in (("(", ")"), ("[", "]"), ("{", "}"), ("⟨", "⟩"))):
                    continue
                candidates.append(
                    {
                        "path": str(path.relative_to(source_root)),
                        "line": index + 1,
                        "declaration_line": start + 1,
                        "proof_end_line": next_top,
                        "indent": match.group("indent"),
                        "verb": match.group("verb"),
                        "action": action,
                    }
                )
    return candidates


def sample_candidates(candidates: list[dict[str, Any]], n: int = 100) -> list[dict[str, Any]]:
    """Deterministic family-balanced sample, capped at 15 transitions per file."""
    rng = random.Random(SEED)
    buckets: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        buckets.setdefault(candidate["verb"], []).append(candidate)
    for bucket in buckets.values():
        rng.shuffle(bucket)
    verbs = sorted(buckets)
    rng.shuffle(verbs)
    chosen: list[dict[str, Any]] = []
    file_counts: dict[str, int] = {}
    cursor = {verb: 0 for verb in verbs}
    while len(chosen) < n:
        changed = False
        for verb in verbs:
            bucket = buckets[verb]
            while cursor[verb] < len(bucket):
                item = bucket[cursor[verb]]
                cursor[verb] += 1
                if file_counts.get(item["path"], 0) >= 15:
                    continue
                chosen.append(item)
                file_counts[item["path"]] = file_counts.get(item["path"], 0) + 1
                changed = True
                break
            if len(chosen) == n:
                break
        if not changed:
            raise RuntimeError(f"only found {len(chosen)} eligible transitions")
    chosen.sort(key=lambda item: (item["path"], item["line"]))
    for index, item in enumerate(chosen, 1):
        item["id"] = f"ns{index:03d}"
    return chosen


TRACE_HELPER = [
    "",
    "open Lean Elab Tactic Meta in",
    'elab "jev_trace_before " tag:ident : tactic => withMainContext do',
    "  let rendered ← Meta.ppGoal (← getMainGoal)",
    '  logInfo m!"JEV_BEFORE::{tag.getId}\\n{rendered}"',
    "",
    "open Lean Elab Tactic Meta in",
    'elab "jev_trace_after " tag:ident : tactic => withMainContext do',
    "  let rendered ← Meta.ppGoal (← getMainGoal)",
    '  logInfo m!"JEV_AFTER::{tag.getId}\\n{rendered}"',
    "",
]


def inject_trace_helper(lines: list[str]) -> list[str]:
    last_import = max(index for index, line in enumerate(lines) if line.startswith("import "))
    result = list(lines)
    result[last_import + 1 : last_import + 1] = TRACE_HELPER
    return result


def run_lean(source_root: Path, text: str, timeout: float = 300.0) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile("w", suffix=".lean", encoding="utf-8", delete=False) as handle:
        handle.write(text)
        path = Path(handle.name)
    try:
        return subprocess.run(
            ["lake", "env", "lean", str(path)],
            cwd=source_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    finally:
        path.unlink(missing_ok=True)


def instrument_file(source_root: Path, relative: str, items: list[dict[str, Any]]) -> dict[str, str]:
    lines = (source_root / relative).read_text(encoding="utf-8").splitlines()
    insertions = {item["line"] - 1: item for item in items}
    output: list[str] = []
    for index, line in enumerate(lines):
        if index in insertions:
            item = insertions[index]
            output.append(item["indent"] + f"jev_trace_before {item['id']}")
        output.append(line)
    output = inject_trace_helper(output)
    result = run_lean(source_root, "\n".join(output) + "\n", timeout=600.0)
    if result.returncode != 0:
        if len(items) == 1:
            print(f"discarding syntactically nested transition {items[0]['id']} from {relative}")
            return {}
        middle = len(items) // 2
        return {
            **instrument_file(source_root, relative, items[:middle]),
            **instrument_file(source_root, relative, items[middle:]),
        }
    states: dict[str, str] = {}
    marker = re.compile(r"^JEV_BEFORE::(ns\d+)$")
    current: str | None = None
    collected: list[str] = []
    for line in result.stdout.splitlines():
        found = marker.match(line)
        if found:
            if current is not None:
                states[current] = "\n".join(collected).strip()
            current = found.group(1)
            collected = []
        elif current is not None:
            if re.match(r"^/.+\.lean:\d+:\d+: (?:warning|error):", line) or line.startswith("warning: "):
                states[current] = "\n".join(collected).strip()
                current = None
                collected = []
            else:
                collected.append(line)
    if current is not None:
        states[current] = "\n".join(collected).strip()
    missing = [item["id"] for item in items if not states.get(item["id"])]
    if missing:
        print(f"discarding {len(missing):3d} unreachable transitions from {relative}")
    return states


def reconstruct_states(source_root: Path, items: list[dict[str, Any]]) -> dict[str, str]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(item["path"], []).append(item)
    states: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(instrument_file, source_root, path, group): path for path, group in grouped.items()}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            states.update(result)
            print(f"reconstructed {len(result):3d} states from {futures[future]}")
    return states


def state_locals(state: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for line in state.splitlines():
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_']*)\s*:\s*(.+)$", line.strip())
        if match and match.group(1) not in {"case", "this"}:
            result.append((match.group(1), match.group(2)))
    return result


def action_origin(action: str) -> str:
    verb = action.split(maxsplit=1)[0]
    if verb in {"exact", "apply", "rw", "simp"}:
        return "local_or_library"
    if verb in {"cases", "induction", "constructor", "ext", "funext", "left", "right"}:
        return "structural"
    return "tactic_family"


def generated_options(item: dict[str, Any], state: str) -> list[dict[str, Any]]:
    ranked: list[str] = [item["action"]]
    locals_ = state_locals(state)
    hypotheses = [(name, typ) for name, typ in locals_ if name.startswith("h")][:2]
    variables = [(name, typ) for name, typ in locals_ if not name.startswith("h")][:2]
    for name, typ in hypotheses:
        ranked.extend([f"exact {name}", f"apply {name}"])
        if "=" in typ or "↔" in typ:
            ranked.append(f"rw [{name}]")
    for name, typ in variables:
        if any(token in typ for token in ("List", "Nat", "Finset", "Set", "Option", "Sum", "×")):
            ranked.extend([f"cases {name}", f"induction {name}"])
            break
    ranked.extend(["assumption", "constructor", "simp", "aesop", "omega", "rfl"])
    query = state.replace("⊢", " ")
    for declaration in retrieve(query, 2, load_index()):
        ranked.append(f"apply {declaration['name']}")
    deduplicated: list[str] = []
    for action in ranked:
        if action not in deduplicated:
            deduplicated.append(action)
    deduplicated = deduplicated[:MAX_OPTIONS]
    if item["action"] not in deduplicated:
        raise AssertionError("recorded action was lost")
    rng = random.Random(f"{SEED}:{item['id']}")
    shuffled = list(enumerate(deduplicated))
    rng.shuffle(shuffled)
    return [
        {
            "id": f"A{position:02d}",
            "tactic": action,
            "origin": action_origin(action),
            "generation_rank": generation_rank,
        }
        for position, (generation_rank, action) in enumerate(shuffled, 1)
    ]


def build_dataset(source_root: Path) -> dict[str, Any]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=source_root, text=True, stdout=subprocess.PIPE, check=False
    ).stdout.strip()
    if revision and revision != SOURCE_REVISION:
        raise RuntimeError(f"Sipser revision is {revision}, expected {SOURCE_REVISION}")
    sampled = sample_candidates(source_candidates(source_root), 140)
    states = reconstruct_states(source_root, sampled)
    sampled = [item for item in sampled if states.get(item["id"])][:100]
    if len(sampled) != 100:
        raise RuntimeError(f"only reconstructed {len(sampled)} reachable sampled transitions")
    cases = []
    for item in sampled:
        source_path = source_root / item["path"]
        state = states[item["id"]]
        options = generated_options(item, state)
        recorded_option = next(option["id"] for option in options if option["tactic"] == item["action"])
        cases.append(
            {
                **item,
                "source_sha256": sha256_bytes(source_path.read_bytes()),
                "pre_state": state,
                "pre_state_sha256": sha256_bytes(state.encode()),
                "options": options,
                "recorded_option": recorded_option,
            }
        )
    data = {
        "schema_version": 1,
        "seed": SEED,
        "sample_size": len(cases),
        "sampling": "Family-balanced deterministic sample from complete one-line tactic commands at the base indentation of theorem/lemma proof blocks; source files over 30 KB, nested bullet/case commands, unbalanced delimiters, multiline tactic/layout openers, and trailing combinators are excluded; at most 15 samples per file.",
        "source": {
            "project": "sipser",
            "url": SOURCE_URL,
            "revision": SOURCE_REVISION,
            "lean_toolchain": "leanprover/lean4:v4.30.0",
            "mathlib_revision": "c5ea00351c28e24afc9f0f84379aa41082b1188f",
        },
        "option_policy": {"maximum": MAX_OPTIONS, "shuffle_seed": SEED, "library_retrieval_limit": 2},
        "cases": cases,
    }
    DATA_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return data


def payload_for(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": {
            "language": "Lean 4 with pinned Mathlib",
            "proof_state": case["pre_state"],
            "source_context": {"project": "sipser", "path": case["path"]},
        },
        "model": MODEL,
        "questions": {
            "decision": {
                "type": "choice",
                "instructions": {
                    "question": "Which listed tactic is the best next action toward a complete proof of the theorem?",
                    "constraints": [
                        "Choose exactly one listed action and use Lean 4 tactic semantics.",
                        "Judge progress toward completing the whole proof, not whether the action immediately closes the current goal.",
                        "A structural action that creates useful successor goals can be better than a tactic aimed at immediate closure.",
                    ],
                },
                "criteria": {
                    option["id"]: {"lean_action": option["tactic"], "candidate_source": option["origin"]}
                    for option in case["options"]
                },
            }
        },
    }


def freeze() -> dict[str, Any]:
    data = load_data()
    if data["sample_size"] != 100 or len(data["cases"]) != 100:
        raise ValueError("the benchmark must contain exactly 100 cases")
    manifest = {
        "schema_version": 1,
        "model": MODEL,
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "policy": "The dataset, reconstructed states, option sets, shuffle, and prompts were committed before any live next-step-100 request.",
        "data_sha256": sha256_bytes(DATA_PATH.read_bytes()),
        "requests": [
            {"case_id": case["id"], "request_sha256": payload_hash(payload_for(case))} for case in data["cases"]
        ],
    }
    FREEZE_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def verify_freeze(data: dict[str, Any]) -> None:
    manifest = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    if manifest["model"] != MODEL or manifest["data_sha256"] != sha256_bytes(DATA_PATH.read_bytes()):
        raise ValueError("frozen model or dataset hash mismatch")
    expected = {item["case_id"]: item["request_sha256"] for item in manifest["requests"]}
    actual = {case["id"]: payload_hash(payload_for(case)) for case in data["cases"]}
    if expected != actual:
        raise ValueError("frozen requests do not reconstruct")


def validate_response(response: dict[str, Any], payload: dict[str, Any]) -> None:
    if response.get("model") != MODEL:
        raise ValueError(f"resolved model {response.get('model')!r} is not {MODEL!r}")
    answer = response.get("answers", {}).get("decision")
    options = set(payload["questions"]["decision"]["criteria"])
    if not isinstance(answer, dict) or answer.get("type") != "choice" or answer.get("choice") not in options:
        raise ValueError("malformed choice response")
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != options:
        raise ValueError("choice probabilities do not match options")
    if abs(sum(float(value) for value in probabilities.values()) - 1.0) > 0.011:
        raise ValueError("choice probabilities do not sum to one")


def live(trace_path: Path) -> list[dict[str, Any]]:
    data = load_data()
    verify_freeze(data)
    client = TypeSafeClient(os.environ.get("TYPESAFE_API_KEY", ""))
    records = []
    for case in data["cases"]:
        payload = payload_for(case)
        raw, response, latency = client.evaluate(payload)
        validate_response(response, payload)
        records.append(
            {
                "schema_version": 1,
                "case_id": case["id"],
                "requested_model": MODEL,
                "resolved_model": response["model"],
                "request_sha256": payload_hash(payload),
                "raw_response_sha256": sha256_bytes(raw.encode()),
                "latency_seconds": round(latency, 6),
                "raw_response": raw,
            }
        )
        print(f"{case['id']}: {response['answers']['decision']['choice']}")
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return records


def load_trace(data: dict[str, Any], trace_path: Path) -> list[dict[str, Any]]:
    verify_freeze(data)
    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line]
    if [record["case_id"] for record in records] != [case["id"] for case in data["cases"]]:
        raise ValueError("trace case order differs from frozen benchmark")
    by_id = {case["id"]: case for case in data["cases"]}
    for record in records:
        raw = record["raw_response"]
        if sha256_bytes(raw.encode()) != record["raw_response_sha256"]:
            raise ValueError("response content hash mismatch")
        payload = payload_for(by_id[record["case_id"]])
        if payload_hash(payload) != record["request_sha256"]:
            raise ValueError("request content hash mismatch")
        response = json.loads(raw)
        validate_response(response, payload)
        record["response"] = response
    return records


def variant_text(source_root: Path, case: dict[str, Any], action: str, mode: str) -> str:
    path = source_root / case["path"]
    if sha256_bytes(path.read_bytes()) != case["source_sha256"]:
        raise ValueError(f"source hash mismatch for {case['path']}")
    lines = path.read_text(encoding="utf-8").splitlines()
    index = case["line"] - 1
    if lines[index].strip() != case["action"]:
        raise ValueError(f"recorded source action mismatch for {case['id']}")
    if mode == "continuation":
        lines[index] = case["indent"] + action
    elif mode == "isolated":
        del lines[index + 1 : case["proof_end_line"]]
        lines[index] = case["indent"] + action
        lines.insert(index + 1, case["indent"] + f"all_goals (jev_trace_after {case['id']}; sorry)")
        lines.insert(index + 2, "  all_goals sorry")
    else:
        raise ValueError(mode)
    return "\n".join(inject_trace_helper(lines)) + "\n"


def after_states(output: str, case_id: str) -> list[str]:
    marker = f"JEV_AFTER::{case_id}"
    lines = output.splitlines()
    result: list[str] = []
    for index, line in enumerate(lines):
        if line != marker:
            continue
        collected = []
        for following in lines[index + 1 :]:
            if following.startswith("JEV_AFTER::") or re.match(r"^/.+\.lean:\d+:\d+: (?:warning|error):", following):
                break
            collected.append(following)
        result.append("\n".join(collected).strip())
    return [state for state in result if state]


def classify_one(source_root: Path, case: dict[str, Any], selected: str) -> dict[str, Any]:
    isolated = run_lean(source_root, variant_text(source_root, case, selected, "isolated"), timeout=600.0)
    if isolated.returncode != 0:
        return {"classification": "invalid", "isolated_returncode": isolated.returncode, "after_states": []}
    states = after_states(isolated.stdout, case["id"])
    if states and states[0] == case["pre_state"]:
        return {"classification": "no_progress", "isolated_returncode": 0, "after_states": states}
    continuation = run_lean(source_root, variant_text(source_root, case, selected, "continuation"), timeout=600.0)
    if continuation.returncode == 0:
        classification = "verified_alternative"
        verification = "substituting the selected action and retaining the recorded suffix compiled the theorem"
    else:
        classification = "valid_unverified_continuation"
        verification = "Lean accepted the action, but the recorded suffix did not verify the theorem"
    return {
        "classification": classification,
        "verification": verification,
        "isolated_returncode": 0,
        "continuation_returncode": continuation.returncode,
        "after_states": states,
    }


def check_selections(source_root: Path, trace_path: Path, outcome_path: Path) -> dict[str, Any]:
    data = load_data()
    records = load_trace(data, trace_path)
    responses = {record["case_id"]: record["response"] for record in records}
    misses = []
    for case in data["cases"]:
        choice = responses[case["id"]]["answers"]["decision"]["choice"]
        if choice != case["recorded_option"]:
            selected = next(option["tactic"] for option in case["options"] if option["id"] == choice)
            misses.append((case, choice, selected))
    results: dict[str, Any] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(classify_one, source_root, case, selected): (case, choice, selected)
            for case, choice, selected in misses
        }
        for future in concurrent.futures.as_completed(futures):
            case, choice, selected = futures[future]
            result = future.result()
            results[case["id"]] = {"selected_option": choice, "selected_action": selected, **result}
            print(f"{case['id']}: {result['classification']}")
    artifact = {
        "schema_version": 1,
        "criteria": {
            "invalid": "The isolated prefix plus selected action failed in Lean before successor goals could be admitted.",
            "no_progress": "Lean accepted the action, but its first printed successor proof state was byte-identical to the frozen pre-state.",
            "valid_unverified_continuation": "Lean accepted the action and changed the state, but substituting it for the recorded action while retaining the recorded proof suffix did not compile the theorem.",
            "verified_alternative": "Substituting the selected action and retaining the recorded proof suffix compiled the theorem.",
            "priority": ["invalid", "no_progress", "verified_alternative", "valid_unverified_continuation"],
        },
        "source_revision": SOURCE_REVISION,
        "misses": results,
    }
    outcome_path.parent.mkdir(parents=True, exist_ok=True)
    outcome_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return artifact


def metrics(trace_path: Path, outcome_path: Path, metrics_path: Path) -> dict[str, Any]:
    data = load_data()
    trace = load_trace(data, trace_path)
    outcomes = json.loads(outcome_path.read_text(encoding="utf-8"))
    responses = {record["case_id"]: record for record in trace}
    rng = random.Random(SEED)
    rows = []
    baseline_hits = {"random_seeded": 0, "deterministic": 0, "aesop_first": 0, "oracle": 0}
    for case in data["cases"]:
        answer = responses[case["id"]]["response"]["answers"]["decision"]
        choice = answer["choice"]
        exact = choice == case["recorded_option"]
        if exact:
            classification = "recorded_exact"
            semantic = True
        else:
            classification = outcomes["misses"][case["id"]]["classification"]
            semantic = classification == "verified_alternative"
        deterministic_priority = ["rfl", "simp", "assumption", "constructor", "omega", "aesop"]
        deterministic = next(
            (
                option["id"]
                for tactic in deterministic_priority
                for option in case["options"]
                if option["tactic"] == tactic
            ),
            min(case["options"], key=lambda option: option["tactic"])["id"],
        )
        aesop = next((option["id"] for option in case["options"] if option["tactic"] == "aesop"), deterministic)
        random_choice = rng.choice(case["options"])["id"]
        baseline_choices = {"random_seeded": random_choice, "deterministic": deterministic, "aesop_first": aesop, "oracle": case["recorded_option"]}
        for name, baseline_choice in baseline_choices.items():
            baseline_hits[name] += baseline_choice == case["recorded_option"]
        rows.append(
            {
                "case_id": case["id"],
                "path": case["path"],
                "line": case["line"],
                "recorded_action": case["action"],
                "selected_option": choice,
                "selected_action": next(option["tactic"] for option in case["options"] if option["id"] == choice),
                "selected_probability": answer["probabilities"][choice],
                "exact_match": exact,
                "classification": classification,
                "semantically_acceptable": semantic,
            }
        )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    input_tokens = sum(record["response"].get("usage", {}).get("input_tokens", 0) for record in trace)
    output_tokens = sum(record["response"].get("usage", {}).get("output_tokens", 0) for record in trace)
    result = {
        "schema_version": 1,
        "model": MODEL,
        "n": len(rows),
        "exact_match": sum(row["exact_match"] for row in rows),
        "semantic_acceptability": sum(row["semantically_acceptable"] for row in rows),
        "miss_classifications": counts,
        "baselines_exact_match": baseline_hits,
        "random_exact_expectation": sum(1 / len(case["options"]) for case in data["cases"]),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_input_cost_usd_at_0_042_per_million": round(input_tokens * 0.042 / 1_000_000, 9),
        "actual_billed_cost_usd": None,
        "latency_seconds_total": round(sum(record["latency_seconds"] for record in trace), 6),
        "rows": rows,
    }
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return result


def verify_states(source_root: Path) -> None:
    data = load_data()
    sampled = [{key: case[key] for key in ("id", "path", "line", "indent")} for case in data["cases"]]
    states = reconstruct_states(source_root, sampled)
    mismatches = [case["id"] for case in data["cases"] if states[case["id"]] != case["pre_state"]]
    if mismatches:
        raise RuntimeError(f"reconstructed state mismatch: {mismatches}")
    print("verified 100 frozen pre-step states")


def main() -> None:
    parser = argparse.ArgumentParser(description="Jev next-step prediction on 100 real Sipser transitions")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-dataset")
    build.add_argument("--source-root", type=Path, required=True)
    sub.add_parser("freeze")
    live_parser = sub.add_parser("live")
    live_parser.add_argument("--results-dir", type=Path, required=True)
    verify = sub.add_parser("verify-states")
    verify.add_argument("--source-root", type=Path, required=True)
    check = sub.add_parser("check-selections")
    check.add_argument("--source-root", type=Path, required=True)
    check.add_argument("--results-dir", type=Path, required=True)
    metrics_parser = sub.add_parser("metrics")
    metrics_parser.add_argument("--results-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "build-dataset":
        print(f"wrote {build_dataset(args.source_root)['sample_size']} cases")
    elif args.command == "freeze":
        print(f"froze {len(freeze()['requests'])} requests")
    elif args.command == "live":
        paths = result_paths(args.results_dir)
        print(f"wrote {len(live(paths['trace']))} live responses")
    elif args.command == "verify-states":
        verify_states(args.source_root)
    elif args.command == "check-selections":
        paths = result_paths(args.results_dir)
        print(f"classified {len(check_selections(args.source_root, paths['trace'], paths['outcomes'])['misses'])} misses")
    else:
        paths = result_paths(args.results_dir)
        print(json.dumps(metrics(paths['trace'], paths['outcomes'], paths['metrics']), indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
