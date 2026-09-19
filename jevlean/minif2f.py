"""Leakage-controlled miniF2F Lean 4 Test pilot runner.

The committed manifest vendors only ported formal statements.  It never stores
miniF2F docstrings or proof bodies, and each worker sees one standalone target.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

from . import benchmark4

ROOT = Path(__file__).resolve().parents[1]
PILOT_PATH = ROOT / "data/minif2f-test-4.30-pilot.json"
UPSTREAM_URL = "https://github.com/google-deepmind/miniF2F"
UPSTREAM_COMMIT = "f0a20e14c1eeccd859d51bb4c2b3ee487889c303"
UPSTREAM_TEST_SHA256 = "8e135204947654a6f5234ee3da830ea7b72337f33dc29229fd8be6c69ac1c0b7"
UPSTREAM_PROBLEM_IMPORTS_SHA256 = "ed4eb9efdfe3698c65ccd225e804a32f4027f32d64e47376df20681f268024d6"
MATHLIB_COMMIT = "c5ea00351c28e24afc9f0f84379aa41082b1188f"
LEAN_TOOLCHAIN = "leanprover/lean4:v4.30.0"


class MiniF2FError(RuntimeError):
    """A frozen miniF2F input or execution artifact is invalid."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def target_id(name: str) -> str:
    return "mf2f-" + sha256(name.encode())[:16]


def _theorem_headers(source: str) -> list[dict[str, str]]:
    """Extract declaration headers without adjacent documentation or proof text."""
    starts = list(re.finditer(r"(?m)^theorem\s+([A-Za-z0-9_]+)\b", source))
    targets: list[dict[str, str]] = []
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(source)
        command = source[match.start():end]
        delimiter, retained = benchmark4._proof_delimiter(command)
        if retained != 2:
            raise MiniF2FError(f"{match.group(1)} has an unsupported equation-style declaration")
        statement = command[:delimiter + retained].rstrip()
        if "/--" in statement or "sorry" in statement:
            raise MiniF2FError(f"{match.group(1)} statement extraction includes forbidden text")
        targets.append({
            "id": target_id(match.group(1)),
            "name": match.group(1),
            "statement": statement,
            "statement_sha256": sha256(statement.encode()),
            "port": "accepted" if False else "unclassified",
        })
    return targets


def build_pilot(upstream_root: Path, count: int = 75) -> dict[str, Any]:
    """Port all upstream Test declarations and freeze the hash-first pilot.

    This function extracts statements only.  The caller must separately run
    ``verify_port`` before treating its output as a ported manifest.
    """
    test_path = upstream_root / "MiniF2F/Test.lean"
    imports_path = upstream_root / "MiniF2F/ProblemImports.lean"
    if sha256(test_path.read_bytes()) != UPSTREAM_TEST_SHA256:
        raise MiniF2FError("MiniF2F/Test.lean does not match the frozen upstream commit")
    if sha256(imports_path.read_bytes()) != UPSTREAM_PROBLEM_IMPORTS_SHA256:
        raise MiniF2FError("MiniF2F/ProblemImports.lean does not match the frozen upstream commit")
    all_targets = _theorem_headers(test_path.read_text(encoding="utf-8"))
    all_targets.sort(key=lambda target: (sha256(target["name"].encode()), target["name"]))
    accepted = all_targets[:count]
    excluded = [{"id": target["id"], "name": target["name"], "reason": "selection cutoff"}
                for target in all_targets[count:]]
    for target in accepted:
        target["port"] = "accepted"
    manifest = {
        "schema": 1,
        "benchmark": "Google DeepMind miniF2F Lean 4",
        "split": "Test",
        "upstream_url": UPSTREAM_URL,
        "upstream_commit": UPSTREAM_COMMIT,
        "upstream_test_sha256": UPSTREAM_TEST_SHA256,
        "upstream_problem_imports_sha256": UPSTREAM_PROBLEM_IMPORTS_SHA256,
        "lean_toolchain": LEAN_TOOLCHAIN,
        "mathlib_commit": MATHLIB_COMMIT,
        "selection": "SHA-256 canonical theorem name order; first 75 successful 4.30 ports",
        "premise_policy": "Mathlib declarations only",
        "accepted_targets": accepted,
        "excluded_targets": excluded,
        "port_check": {"candidate_count": len(all_targets), "all_candidates_build": True},
    }
    manifest["accepted_targets_sha256"] = sha256(canonical_json(accepted))
    manifest["excluded_targets_sha256"] = sha256(canonical_json(excluded))
    return manifest


def _validate_manifest(pilot: dict[str, Any]) -> None:
    accepted, excluded = pilot.get("accepted_targets"), pilot.get("excluded_targets")
    if not isinstance(accepted, list) or len(accepted) != 75:
        raise MiniF2FError("pilot must contain exactly 75 accepted targets")
    if not isinstance(excluded, list):
        raise MiniF2FError("pilot exclusions are missing")
    names = [target.get("name") for target in accepted] + [target.get("name") for target in excluded]
    if len(names) != len(set(names)) or len(names) != 244:
        raise MiniF2FError("pilot target universe is not the frozen Test split")
    for target in accepted:
        statement = target.get("statement")
        if not isinstance(statement, str) or not statement.startswith("theorem ") or not statement.endswith(":="):
            raise MiniF2FError("accepted target has no standalone formal statement")
        if any(forbidden in statement for forbidden in ("/--", "sorry", "by\n")):
            raise MiniF2FError("accepted target contains documentation or proof text")
        if target.get("id") != target_id(target["name"]):
            raise MiniF2FError("accepted target identity is invalid")
        if target.get("statement_sha256") != sha256(statement.encode()):
            raise MiniF2FError("accepted target statement digest is invalid")
    if pilot.get("port_check") != {"candidate_count": 244, "all_candidates_build": True}:
        raise MiniF2FError("pilot port verification record is invalid")
    if pilot.get("accepted_targets_sha256") != sha256(canonical_json(accepted)):
        raise MiniF2FError("accepted target digest is invalid")
    if pilot.get("excluded_targets_sha256") != sha256(canonical_json(excluded)):
        raise MiniF2FError("excluded target digest is invalid")


def load_pilot(path: Path = PILOT_PATH) -> dict[str, Any]:
    pilot = json.loads(path.read_text(encoding="utf-8"))
    _validate_manifest(pilot)
    return pilot


def materialize_target(target: dict[str, Any], *, search_import: str = "JevLean",
                       tactic: str = "jev_benchmark?", proof: str | None = None) -> tuple[str, dict[str, Any]]:
    """Construct the only Lean source visible to a target worker."""
    statement = target["statement"]
    if search_import and not re.fullmatch(r"[A-Za-z0-9_.]+", search_import):
        raise MiniF2FError("search import is not a Lean module name")
    imports = "import Mathlib\n" + (f"import {search_import}\n" if search_import else "")
    source = imports + "\nopen scoped Nat\nopen scoped Real\n\n" + statement + " by\n  " + (proof if proof is not None else tactic).replace("\n", "\n  ") + "\n"
    audit = {
        "statement_sha256": sha256(statement.encode()),
        "materialized_sha256": sha256(source.encode()),
        "upstream_proof_included": False,
        "upstream_docstring_included": False,
        "benchmark_declarations_available_before_proof": False,
        "later_declarations_available_before_proof": False,
        "allowed_premise_scope": "Mathlib",
    }
    return source, audit


def verify_port(upstream_root: Path, search_project: Path) -> None:
    """Build all upstream Test statements under the pinned Mathlib import surface."""
    pilot = build_pilot(upstream_root)
    headers = _theorem_headers((upstream_root / "MiniF2F/Test.lean").read_text(encoding="utf-8"))
    source = "import Mathlib\n\nopen scoped Nat\nopen scoped Real\n\n" + "\n".join(
        target["statement"] + " by\n  sorry\n" for target in headers
    )
    with tempfile.TemporaryDirectory(prefix="jevlean-minif2f-port-") as directory:
        path = Path(directory) / "PortCheck.lean"
        path.write_text(source, encoding="utf-8")
        run = benchmark4._run_process(["lake", "env", "lean", str(path)], search_project, 600,
                                      benchmark4.credential_free_env())
    if run["returncode"] != 0 or run["timed_out"]:
        raise MiniF2FError("not every frozen Test statement ports to the current Mathlib pin")
    if pilot["port_check"]["candidate_count"] != len(headers):
        raise MiniF2FError("port check candidate count changed")


def audit_environment(search_project: Path) -> dict[str, Any]:
    toolchain_path = search_project / "lean-toolchain"
    actual_toolchain = toolchain_path.read_text().strip() if toolchain_path.is_file() else None
    actual_mathlib = benchmark4._git_revision(search_project / ".lake/packages/mathlib")
    blockers = []
    if actual_toolchain != LEAN_TOOLCHAIN:
        blockers.append(f"search toolchain is {actual_toolchain!r}; miniF2F requires {LEAN_TOOLCHAIN!r}")
    if actual_mathlib != MATHLIB_COMMIT:
        blockers.append(f"Mathlib checkout is {actual_mathlib!r}; miniF2F requires {MATHLIB_COMMIT!r}")
    return {"schema": 1, "compatible": not blockers,
            "actual": {"lean_toolchain": actual_toolchain, "mathlib_commit": actual_mathlib},
            "blockers": blockers}


def acquire(output: Path) -> Path:
    """Acquire the pinned upstream only to audit it; statements are already vendored."""
    checkout = output / "miniF2F"
    if not checkout.exists():
        subprocess.run(["git", "clone", UPSTREAM_URL, str(checkout)], check=True)
    subprocess.run(["git", "-C", str(checkout), "checkout", "--detach", UPSTREAM_COMMIT], check=True)
    pilot = build_pilot(checkout)
    audit = {"upstream_commit": benchmark4._git_revision(checkout),
             "upstream_test_sha256": UPSTREAM_TEST_SHA256,
             "extracted_target_count": len(pilot["accepted_targets"]) + len(pilot["excluded_targets"])}
    output.mkdir(parents=True, exist_ok=True)
    (output / "upstream-audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    return checkout


def _sandboxed_command(search_project: Path, workspace: Path, source_name: str) -> tuple[list[str], dict[str, str]]:
    """Run a standalone target without mounting any miniF2F source or manifest."""
    bwrap = Path("/usr/bin/bwrap")
    if not bwrap.is_file():
        raise MiniF2FError("bubblewrap is required for leakage-controlled execution")
    lean, lean_path = benchmark4._search_environment(str(search_project.resolve()))
    toolchain = lean.parents[1]
    command = [str(bwrap), "--die-with-parent", "--new-session"]
    for host_path in (Path("/usr"), Path("/lib"), Path("/lib64")):
        if host_path.exists():
            command += ["--ro-bind", str(host_path), str(host_path)]
    command += ["--dir", "/etc"]
    for host_path in (Path("/etc/ld.so.cache"), Path("/etc/localtime"), Path("/etc/hosts")):
        if host_path.exists():
            command += ["--ro-bind", str(host_path), str(host_path)]
    lean_cache = Path("/opt/bots/lean")
    if lean_cache.exists():
        command += benchmark4._directory_mounts(lean_cache)
        command += ["--ro-bind", str(lean_cache), str(lean_cache)]
    command += benchmark4._directory_mounts(toolchain)
    command += ["--ro-bind", str(toolchain), str(toolchain)]
    command += benchmark4._directory_mounts(search_project)
    command += ["--ro-bind", str(search_project.resolve()), str(search_project.resolve())]
    hidden_data = workspace / "empty-data"
    hidden_data.mkdir(exist_ok=True)
    command += ["--ro-bind", str(hidden_data), str(search_project.resolve() / "data")]
    command += ["--bind", str(workspace), "/workspace", "--proc", "/proc", "--dev", "/dev",
                "--tmpfs", "/tmp", "--dir", "/tmp/home", "--chdir", str(search_project.resolve()),
                "--clearenv", "--setenv", "HOME", "/tmp/home", "--setenv", "PATH", f"{toolchain}/bin:/usr/bin:/bin",
                "--setenv", "LEAN_PATH", lean_path, "--setenv", "LANG", "C.UTF-8"]
    for name in ("JEV_MODEL_BROKER_PORT", "JEV_RANK_BROKER_PORT", "JEV_HELPER_BROKER_PORT",
                 "JEV_LLM_HELPERS"):
        value = __import__("os").environ.get(name)
        if value is not None:
            command += ["--setenv", name, value]
    return command + [str(lean), f"/workspace/{source_name}"], {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8"}


def run_target(target: dict[str, Any], search_project: Path, timeout: float,
               search_import: str = "JevLean", tactic: str = "jev_benchmark?") -> dict[str, Any]:
    """Search then replay in fresh credential-free processes under one deadline."""
    import time
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"jevlean-{target['id']}-") as directory:
        workspace = Path(directory)
        source, audit = materialize_target(target, search_import=search_import, tactic=tactic)
        (workspace / "Search.lean").write_text(source)
        command, env = _sandboxed_command(search_project, workspace, "Search.lean")
        search = benchmark4._run_process(command, search_project, timeout, env)
        marker = benchmark4._parse_result(search["output"])
        proof = marker.get("proof") if marker else None
        replay = {"attempted": False, "accepted": False}
        replay_run: dict[str, Any] | None = None
        remaining = timeout - (time.monotonic() - started)
        if isinstance(proof, str) and proof.strip() and not search["timed_out"] and remaining > 0:
            replay_source, replay_audit = materialize_target(target, search_import="", proof=proof)
            if replay_audit["statement_sha256"] != audit["statement_sha256"]:
                raise MiniF2FError("fresh replay statement differs from search statement")
            (workspace / "Replay.lean").write_text(replay_source)
            command, env = _sandboxed_command(search_project, workspace, "Replay.lean")
            replay_run = benchmark4._run_process(command, search_project, remaining, env)
            replay = {"attempted": True, "accepted": replay_run["returncode"] == 0 and not replay_run["timed_out"],
                      "timed_out": replay_run["timed_out"], "returncode": replay_run["returncode"],
                      "wall_seconds": replay_run["wall_seconds"], "cpu_seconds": replay_run["cpu_seconds"],
                      "output": replay_run["output"]}
        timed_out = search["timed_out"] or bool(replay_run and replay_run["timed_out"])
        solved = bool(marker and marker.get("status") == "solved" and replay["accepted"])
        return {"schema": 1, "task": {key: target[key] for key in ("id", "name", "statement_sha256")},
                "status": "solved" if solved else "timeout" if timed_out else "failed", "verified_solve": solved,
                "generated_proof": proof, "fresh_replay": replay, "leakage_audit": audit,
                "search_process": search, "search_metrics": marker.get("metrics", {}) if marker else {},
                "usage": marker.get("usage", {}) if marker else {}}


def run_pilot(search_project: Path, output: Path, timeout: float, limit: int | None = None,
              search_import: str = "JevLean", tactic: str = "jev_benchmark?") -> dict[str, Any]:
    pilot, environment = load_pilot(), audit_environment(search_project)
    if not environment["compatible"]:
        raise MiniF2FError("; ".join(environment["blockers"]))
    targets = pilot["accepted_targets"][:limit]
    results = []
    for target in targets:
        result_path = output / "tasks" / f"{target['id']}.json"
        if result_path.exists():
            result = json.loads(result_path.read_text())
            if result.get("task", {}).get("id") != target["id"]:
                raise MiniF2FError(f"resume artifact has the wrong task identity: {result_path}")
        else:
            result = run_target(target, search_project, timeout, search_import, tactic)
            benchmark4._atomic_json(result_path, result)
        results.append(result)
    summary = {"schema": 1, "accepted_targets_sha256": pilot["accepted_targets_sha256"], "environment": environment,
               "task_count": len(results), "solved": sum(item["verified_solve"] for item in results),
               "failed": sum(item["status"] == "failed" for item in results), "timed_out": sum(item["status"] == "timeout" for item in results),
               "results": [f"tasks/{item['task']['id']}.json" for item in results]}
    benchmark4._atomic_json(output / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    acquire_parser = commands.add_parser("acquire")
    acquire_parser.add_argument("--output", type=Path, required=True)
    freeze_parser = commands.add_parser("freeze")
    freeze_parser.add_argument("--upstream-root", type=Path, required=True)
    freeze_parser.add_argument("--search-project", type=Path, default=ROOT)
    freeze_parser.add_argument("--output", type=Path, default=PILOT_PATH)
    audit_parser = commands.add_parser("audit")
    audit_parser.add_argument("--search-project", type=Path, default=ROOT)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--search-project", type=Path, default=ROOT)
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--timeout", type=float, default=180.0)
    run_parser.add_argument("--limit", type=int)
    run_parser.add_argument("--search-import", default="JevLean")
    run_parser.add_argument("--tactic", default="jev_benchmark?")
    args = parser.parse_args(argv)
    try:
        if args.command == "acquire":
            print(acquire(args.output))
        elif args.command == "freeze":
            verify_port(args.upstream_root, args.search_project)
            manifest = build_pilot(args.upstream_root)
            args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            print(manifest["accepted_targets_sha256"])
        elif args.command == "audit":
            audit = audit_environment(args.search_project)
            print(json.dumps(audit, indent=2, sort_keys=True))
            return 0 if audit["compatible"] else 2
        else:
            summary = run_pilot(args.search_project, args.output, args.timeout, args.limit,
                                args.search_import, args.tactic)
            print(json.dumps(summary, indent=2, sort_keys=True))
    except (MiniF2FError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({"error": type(error).__name__, "message": str(error)}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
