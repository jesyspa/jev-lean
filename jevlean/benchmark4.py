"""Leakage-controlled LeanDojo Benchmark 4 pilot runner.

The runner uses only the Python standard library.  It consumes the official
export and a checkout of its pinned Mathlib revision; it never consumes traced
proofs while materializing or running a task.
"""

from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
from typing import Any, Iterable
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PILOT_PATH = ROOT / "data/leandojo-benchmark4-novel-premises-pilot.json"
DATASET_URL = (
    "https://zenodo.org/api/records/12740403/files/"
    "leandojo_benchmark_4.tar.gz/content"
)
DATASET_ARCHIVE_MD5 = "25e1ee60cd8925b9d2e8673ddcc34b4c"
TEST_SHA256 = "6c001d1cacd141a4301965fa03cb3c756c19aa09fb30daf9cb2ccb48396ff3fe"
MATHLIB_COMMIT = "29dcec074de168ac2bf835a77ef68bbe069194c5"
LEAN_TOOLCHAIN = "leanprover/lean4:v4.10.0-rc1"
RESULT_MARKER = "JEVLEAN_BENCHMARK_RESULT "
CREDENTIAL_NAMES = (
    "TYPESAFE_API_KEY",
    "OPENROUTER_API_KEY",
    "GITHUB_ACCESS_TOKEN",
    "GH_TOKEN",
)


class BenchmarkError(RuntimeError):
    """A benchmark precondition or artifact is invalid."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def task_id(task: dict[str, Any]) -> str:
    identity = f"{task['file_path']}\0{task['full_name']}".encode()
    return "ld4-" + sha256(identity)[:16]


def selected_tasks(records: Iterable[dict[str, Any]], count: int = 50) -> list[dict[str, Any]]:
    """Select the stable hash-first pilot from repository-owned test tasks."""
    eligible = [r for r in records if r["file_path"].startswith("Mathlib/")]
    eligible.sort(
        key=lambda r: (
            sha256(f"{r['file_path']}\0{r['full_name']}".encode()),
            r["file_path"],
            r["full_name"],
        )
    )
    return [
        {
            "id": task_id(record),
            "file_path": record["file_path"],
            "full_name": record["full_name"],
            "start": record["start"],
            "end": record["end"],
        }
        for record in eligible[:count]
    ]


def build_pilot(dataset_root: Path, count: int = 50) -> dict[str, Any]:
    test_path = dataset_root / "novel_premises/test.json"
    raw = test_path.read_bytes()
    if sha256(raw) != TEST_SHA256:
        raise BenchmarkError("novel_premises/test.json does not match the frozen v10 export")
    records = json.loads(raw)
    tasks = selected_tasks(records, count)
    manifest: dict[str, Any] = {
        "schema": 1,
        "benchmark": "LeanDojo Benchmark 4",
        "zenodo_record": 12740403,
        "dataset_version": "v10",
        "archive_md5": DATASET_ARCHIVE_MD5,
        "split": "novel_premises/test",
        "split_sha256": TEST_SHA256,
        "selection": "first 50 Mathlib/ tasks by sha256(file_path + NUL + full_name)",
        "mathlib_url": "https://github.com/leanprover-community/mathlib4",
        "mathlib_commit": MATHLIB_COMMIT,
        "lean_toolchain": LEAN_TOOLCHAIN,
        "tasks": tasks,
    }
    manifest["tasks_sha256"] = sha256(canonical_json(tasks))
    return manifest


def load_pilot(path: Path = PILOT_PATH) -> dict[str, Any]:
    pilot = json.loads(path.read_text(encoding="utf-8"))
    tasks = pilot.get("tasks")
    if not isinstance(tasks, list) or not 50 <= len(tasks) <= 100:
        raise BenchmarkError("pilot must contain 50 to 100 tasks")
    if pilot.get("tasks_sha256") != sha256(canonical_json(tasks)):
        raise BenchmarkError("pilot task digest does not match")
    ids = [task.get("id") for task in tasks]
    if len(ids) != len(set(ids)) or any(task_id(task) != task["id"] for task in tasks):
        raise BenchmarkError("pilot task identities are invalid")
    return pilot


def _safe_members(archive: tarfile.TarFile, output: Path) -> list[tarfile.TarInfo]:
    root = output.resolve()
    members = archive.getmembers()
    for member in members:
        destination = (output / member.name).resolve()
        if destination != root and root not in destination.parents:
            raise BenchmarkError(f"archive member escapes output directory: {member.name}")
        if member.issym() or member.islnk():
            raise BenchmarkError(f"archive contains a link: {member.name}")
    return members


def acquire(output: Path) -> Path:
    """Download and verify the official credential-free Zenodo export."""
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / "leandojo_benchmark_4.tar.gz"
    if not archive_path.is_file() or hashlib.md5(archive_path.read_bytes()).hexdigest() != DATASET_ARCHIVE_MD5:
        temporary = archive_path.with_suffix(".download")
        request = urllib.request.Request(DATASET_URL, headers={"User-Agent": "jevlean-benchmark4"})
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        if hashlib.md5(temporary.read_bytes()).hexdigest() != DATASET_ARCHIVE_MD5:
            temporary.unlink(missing_ok=True)
            raise BenchmarkError("downloaded archive checksum does not match the v10 record")
        temporary.replace(archive_path)
    dataset_root = output / "leandojo_benchmark_4"
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(output, members=_safe_members(archive, output))
    build_pilot(dataset_root)
    return dataset_root


def _position_offset(source: str, position: list[int]) -> int:
    line, column = position
    if line < 1 or column < 1:
        raise BenchmarkError(f"invalid source position: {position}")
    lines = source.splitlines(keepends=True)
    if line > len(lines):
        raise BenchmarkError(f"source line is out of range: {position}")
    prefix = "".join(lines[: line - 1])
    body = lines[line - 1]
    if column - 1 > len(body):
        raise BenchmarkError(f"source column is out of range: {position}")
    return len(prefix) + column - 1


def _proof_delimiter(command: str) -> tuple[int, int]:
    """Find the declaration value or equation-clause boundary.

    The second component is the number of delimiter characters retained in the
    declaration header.  Equation-style theorem clauses start with a line-level
    ``|`` and are replaced by an ordinary ``:= by`` value.
    """
    candidates: list[int] = []
    equation_candidates: list[int] = []
    stack: list[str] = []
    block_comments = 0
    line_comment = False
    string = False
    escaped = False
    index = 0
    pairs = {")": "(", "]": "[", "}": "{"}
    while index < len(command):
        here = command[index : index + 2]
        char = command[index]
        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if block_comments:
            if here == "/-":
                block_comments += 1
                index += 2
            elif here == "-/":
                block_comments -= 1
                index += 2
            else:
                index += 1
            continue
        if string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                string = False
            index += 1
            continue
        if here == "--":
            line_comment = True
            index += 2
        elif here == "/-":
            block_comments = 1
            index += 2
        elif char == '"':
            string = True
            index += 1
        elif char in "([{":
            stack.append(char)
            index += 1
        elif char in pairs:
            if stack and stack[-1] == pairs[char]:
                stack.pop()
            index += 1
        elif here == ":=" and not stack:
            candidates.append(index)
            index += 2
        elif char == "|" and not stack and command[command.rfind("\n", 0, index) + 1 : index].strip() == "":
            equation_candidates.append(index)
            index += 1
        else:
            index += 1
    if block_comments or string or stack:
        raise BenchmarkError("target command has unbalanced lexical structure")
    if candidates:
        return candidates[-1], 2
    if equation_candidates:
        return equation_candidates[0], 0
    raise BenchmarkError("target command has no top-level proof boundary")


def _inject_import(prefix: str, module: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.]+", module):
        raise BenchmarkError("search import is not a Lean module name")
    lines = prefix.splitlines(keepends=True)
    last_import = -1
    for index, line in enumerate(lines):
        if line.startswith("import "):
            last_import = index
    insertion = last_import + 1
    lines.insert(insertion, f"import {module}\n")
    return "".join(lines)


def materialize_task(
    task: dict[str, Any], mathlib_root: Path, *, search_import: str = "JevLean",
    tactic: str = "jev_benchmark?", proof: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build a prefix-only source file with the target proof replaced.

    No benchmark JSON is read here.  The returned source ends at the target,
    so later commands are absent.  The target declaration does not exist in the
    imported environment while its replacement proof is elaborated.
    """
    source_path = (mathlib_root / task["file_path"]).resolve()
    root = mathlib_root.resolve()
    if root not in source_path.parents or not source_path.is_file():
        raise BenchmarkError(f"task source is unavailable: {task['file_path']}")
    original = source_path.read_text(encoding="utf-8")
    start = _position_offset(original, task["start"])
    end = _position_offset(original, task["end"])
    if not 0 <= start < end <= len(original):
        raise BenchmarkError("target source range is invalid")
    command = original[start:end]
    delimiter, retained = _proof_delimiter(command)
    declaration_header = command[: delimiter + retained].rstrip()
    if retained == 0:
        declaration_header += " :="
    prefix = original[:start]
    prefix = _inject_import(prefix, search_import) if search_import else prefix
    replacement = proof if proof is not None else tactic
    materialized = prefix + declaration_header + " by\n  " + replacement.replace("\n", "\n  ") + "\n"
    audit = {
        "source_path": task["file_path"],
        "prefix_sha256": sha256(prefix.encode()),
        "target_header_sha256": sha256(declaration_header.encode()),
        "materialized_sha256": sha256(materialized.encode()),
        "original_proof_sha256": sha256(command[delimiter + retained :].encode()),
        "original_suffix_included": False,
        "original_proof_included": False,
        "target_declaration_available_before_proof": False,
    }
    return materialized, audit


def _git_revision(path: Path) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _pilot_sources_clean(mathlib_root: Path) -> bool:
    paths = [task["file_path"] for task in load_pilot()["tasks"]]
    completed = subprocess.run(
        ["git", "-C", str(mathlib_root), "diff", "--quiet", "HEAD", "--", *paths],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def audit_environment(search_project: Path, mathlib_root: Path) -> dict[str, Any]:
    expected = {
        "lean_toolchain": LEAN_TOOLCHAIN,
        "mathlib_commit": MATHLIB_COMMIT,
        "pilot_sources_clean": True,
    }
    toolchain_path = search_project / "lean-toolchain"
    actual_toolchain = toolchain_path.read_text().strip() if toolchain_path.is_file() else None
    actual_mathlib = _git_revision(mathlib_root)
    sources_clean = _pilot_sources_clean(mathlib_root)
    blockers = []
    if actual_toolchain != LEAN_TOOLCHAIN:
        blockers.append(
            f"search toolchain is {actual_toolchain!r}; Benchmark 4 requires {LEAN_TOOLCHAIN!r}"
        )
    if actual_mathlib != MATHLIB_COMMIT:
        blockers.append(
            f"Mathlib checkout is {actual_mathlib!r}; Benchmark 4 requires {MATHLIB_COMMIT!r}"
        )
    if not sources_clean:
        blockers.append("one or more frozen pilot source files differ from the pinned Mathlib commit")
    return {
        "schema": 1,
        "compatible": not blockers,
        "expected": expected,
        "actual": {
            "lean_toolchain": actual_toolchain,
            "mathlib_commit": actual_mathlib,
            "pilot_sources_clean": sources_clean,
        },
        "blockers": blockers,
    }


def credential_free_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    for name in CREDENTIAL_NAMES:
        env.pop(name, None)
    return env


def _run_process(command: list[str], cwd: Path, timeout: float, env: dict[str, str]) -> dict[str, Any]:
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.monotonic()
    process = subprocess.Popen(
        command, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, start_new_session=True,
    )
    timed_out = False
    try:
        output, _ = process.communicate(timeout=max(timeout, 0.001))
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate()
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "returncode": process.returncode,
        "timed_out": timed_out,
        "wall_seconds": round(time.monotonic() - started, 6),
        "cpu_seconds": round(
            (after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime), 6
        ),
        "output": output[-12000:],
    }


def _directory_mounts(path: Path) -> list[str]:
    mounts: list[str] = []
    parents = list(path.resolve().parents)
    for parent in reversed(parents[:-1]):
        if parent != Path("/"):
            mounts += ["--dir", str(parent)]
    return mounts


@lru_cache(maxsize=4)
def _search_environment(search_project_text: str) -> tuple[Path, str]:
    search_project = Path(search_project_text)
    environment = subprocess.run(
        ["lake", "env", "sh", "-c", 'command -v lean; printf "%s\\n" "$LEAN_PATH"'],
        cwd=search_project, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
    )
    lines = environment.stdout.strip().splitlines()
    if environment.returncode != 0 or len(lines) < 2:
        raise BenchmarkError(f"cannot locate the search toolchain: {environment.stderr[-2000:]}")
    return Path(lines[-2]).resolve(), lines[-1]


def _sandboxed_lean_command(
    search_project: Path, mathlib_root: Path, workspace: Path,
    source_name: str, task: dict[str, Any], visible_target: str,
) -> tuple[list[str], dict[str, str]]:
    """Construct a hidden-home sandbox with only sanitized target source visible."""
    bwrap = Path("/usr/bin/bwrap")
    if not bwrap.is_file():
        raise BenchmarkError("bubblewrap is required for leakage-controlled execution")
    lean, lean_path = _search_environment(str(search_project.resolve()))
    toolchain = lean.parents[1]
    shadow = workspace / "VisibleTarget.lean"
    shadow.write_text(visible_target, encoding="utf-8")
    target_path = (mathlib_root / task["file_path"]).resolve()
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
        command += _directory_mounts(lean_cache)
        command += ["--ro-bind", str(lean_cache), str(lean_cache)]
    command += _directory_mounts(search_project)
    command += ["--ro-bind", str(search_project.resolve()), str(search_project.resolve())]
    command += _directory_mounts(toolchain)
    if search_project.resolve() not in mathlib_root.resolve().parents and mathlib_root.resolve() != search_project.resolve():
        command += _directory_mounts(mathlib_root)
        command += ["--ro-bind", str(mathlib_root.resolve()), str(mathlib_root.resolve())]
    command += [
        "--ro-bind", str(shadow), str(target_path),
        "--ro-bind", str(toolchain), str(toolchain),
        "--bind", str(workspace), "/workspace",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--dir", "/tmp/home", "--chdir", str(search_project.resolve()),
        "--clearenv", "--setenv", "HOME", "/tmp/home",
        "--setenv", "PATH", f"{toolchain}/bin:/usr/bin:/bin",
        "--setenv", "LEAN_PATH", lean_path,
        "--setenv", "LANG", "C.UTF-8",
    ]
    child_env: dict[str, str] = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8"}
    for name in ("JEV_MODEL_BROKER_PORT", "JEV_RANK_BROKER_PORT", "JEV_HELPER_BROKER_PORT",
                 "JEV_LLM_HELPERS"):
        value = os.environ.get(name)
        if value is not None:
            command += ["--setenv", name, value]
    command += [str(lean), f"/workspace/{source_name}"]
    return command, child_env


def _parse_result(output: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        if RESULT_MARKER in line:
            payload = line.split(RESULT_MARKER, 1)[1]
            try:
                value = json.loads(payload)
            except json.JSONDecodeError as error:
                raise BenchmarkError(f"search emitted malformed result JSON: {error}") from error
            if not isinstance(value, dict):
                raise BenchmarkError("search result marker is not a JSON object")
            return value
    return None


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _search_code_sha256(search_project: Path) -> str:
    """Digest search sources and build configuration, excluding generated output."""
    files = [path for path in search_project.rglob("*.lean") if ".lake" not in path.parts and ".git" not in path.parts]
    files += [search_project / name for name in ("lakefile.toml", "lean-toolchain")]
    contents = [
        {"path": str(path.relative_to(search_project)), "sha256": sha256(path.read_bytes())}
        for path in sorted(files) if path.is_file()
    ]
    return sha256(canonical_json(contents))


def run_identity(adapter: str, task: dict[str, Any], environment: dict[str, Any],
                 search_project: Path, timeout: float, search_import: str, tactic: str) -> dict[str, Any]:
    """Return the complete reproducibility boundary for one reusable result."""
    helpers_enabled = os.environ.get("JEV_LLM_HELPERS") == "1"
    inputs = {
        "schema": 1,
        "adapter": adapter,
        "task": task,
        "environment": environment.get("actual"),
        "search_code_sha256": _search_code_sha256(search_project),
        "search": {"import": search_import, "tactic": tactic},
        "limits": {"hard_timeout_seconds": timeout},
        "observable_provider_config": {
            "helpers_enabled": helpers_enabled,
            "helper_model": os.environ.get("JEV_HELPER_MODEL") if helpers_enabled else None,
            "rank_order": os.environ.get("JEV_RANK_ORDER"),
        },
    }
    return {"schema": 1, "sha256": sha256(canonical_json(inputs)), "inputs": inputs}


def validate_resume(result: dict[str, Any], expected: dict[str, Any], path: Path) -> None:
    actual = result.get("run_identity")
    if actual != expected:
        raise BenchmarkError(
            f"resume artifact is incompatible or legacy: {path}; remove cached task artifacts "
            "or choose a new --output directory to rerun with these settings"
        )


def run_task(
    task: dict[str, Any], mathlib_root: Path, search_project: Path,
    timeout: float, search_import: str, tactic: str,
) -> dict[str, Any]:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"jevlean-{task['id']}-") as directory:
        workspace = Path(directory)
        search_source, leakage = materialize_task(
            task, mathlib_root, search_import=search_import, tactic=tactic
        )
        search_path = workspace / "Search.lean"
        search_path.write_text(search_source, encoding="utf-8")
        visible_target = search_source.replace(f"import {search_import}\n", "", 1)
        search_command, search_env = _sandboxed_lean_command(
            search_project, mathlib_root, workspace, "Search.lean", task, visible_target
        )
        search_remaining = timeout - (time.monotonic() - started)
        search_run = _run_process(search_command, search_project, search_remaining, search_env)
        marker = _parse_result(search_run["output"])
        proof = marker.get("proof") if marker else None
        elapsed = time.monotonic() - started
        replay: dict[str, Any] = {"attempted": False, "accepted": False}
        replay_run = None
        if isinstance(proof, str) and proof.strip() and not search_run["timed_out"]:
            remaining = timeout - elapsed
            if remaining > 0:
                replay_source, replay_leakage = materialize_task(
                    task, mathlib_root, search_import="", proof=proof
                )
                if replay_leakage["target_header_sha256"] != leakage["target_header_sha256"]:
                    raise BenchmarkError("fresh replay target differs from search target")
                replay_path = workspace / "Replay.lean"
                replay_path.write_text(replay_source, encoding="utf-8")
                replay_visible_target = replay_source
                replay_command, replay_env = _sandboxed_lean_command(
                    search_project, mathlib_root, workspace, "Replay.lean", task,
                    replay_visible_target,
                )
                replay_remaining = timeout - (time.monotonic() - started)
                replay_run = _run_process(
                    replay_command, search_project, replay_remaining, replay_env
                )
                replay = {
                    "attempted": True,
                    "accepted": replay_run["returncode"] == 0 and not replay_run["timed_out"],
                    "timed_out": replay_run["timed_out"],
                    "returncode": replay_run["returncode"],
                    "output": replay_run["output"],
                }
        hard_timeout = search_run["timed_out"] or bool(replay_run and replay_run["timed_out"])
        verified = bool(marker and marker.get("status") == "solved" and replay["accepted"])
        status = "solved" if verified else "timeout" if hard_timeout else "failed"
        total_cpu = search_run["cpu_seconds"] + (replay_run or {}).get("cpu_seconds", 0.0)
        return {
            "schema": 1,
            "task": task,
            "status": status,
            "verified_solve": verified,
            "failure": None if verified else (
                "hard process timeout" if hard_timeout else
                marker.get("failure") if marker else "search produced no structured result"
            ),
            "generated_proof": proof,
            "fresh_replay": replay,
            "timing": {
                "wall_seconds": round(time.monotonic() - started, 6),
                "cpu_seconds": round(total_cpu, 6),
                "hard_timeout_seconds": timeout,
            },
            "search_metrics": marker.get("metrics", {}) if marker else {},
            "usage": marker.get("usage", {"jev_calls": 0, "helper_requests": 0, "helper_actions": 0}) if marker else {"jev_calls": 0, "helper_requests": 0, "helper_actions": 0},
            "leakage_audit": leakage,
            "search_process": search_run,
        }


def run_pilot(
    mathlib_root: Path, search_project: Path, output: Path, timeout: float,
    limit: int | None = None, search_import: str = "JevLean", tactic: str = "jev_benchmark?",
) -> dict[str, Any]:
    pilot = load_pilot()
    environment = audit_environment(search_project, mathlib_root)
    if not environment["compatible"]:
        raise BenchmarkError("; ".join(environment["blockers"]))
    _search_environment(str(search_project.resolve()))
    tasks = pilot["tasks"][:limit]
    results_dir = output / "tasks"
    output.mkdir(parents=True, exist_ok=True)
    for task in tasks:
        result_path = results_dir / f"{task['id']}.json"
        _, statement_audit = materialize_task(task, mathlib_root, search_import="", tactic="skip")
        identity = run_identity(
            "benchmark4", {**task, "statement_sha256": statement_audit["target_header_sha256"]},
            environment, search_project, timeout, search_import, tactic,
        )
        if result_path.is_file():
            existing = json.loads(result_path.read_text())
            validate_resume(existing, identity, result_path)
            continue
        try:
            result = run_task(
                task, mathlib_root, search_project, timeout, search_import, tactic
            )
        except (BenchmarkError, OSError, subprocess.SubprocessError) as error:
            result = {
                "schema": 1,
                "task": task,
                "status": "failed",
                "verified_solve": False,
                "failure": f"{type(error).__name__}: {error}",
                "generated_proof": None,
                "fresh_replay": {"attempted": False, "accepted": False},
                "timing": {
                    "wall_seconds": None,
                    "cpu_seconds": None,
                    "hard_timeout_seconds": timeout,
                },
                "search_metrics": {},
                "usage": {"jev_calls": 0, "helper_requests": 0, "helper_actions": 0},
                "leakage_audit": None,
                "search_process": None,
            }
        result["run_identity"] = identity
        _atomic_json(result_path, result)
    results = [json.loads((results_dir / f"{task['id']}.json").read_text()) for task in tasks]
    summary = {
        "schema": 1,
        "pilot_tasks_sha256": pilot["tasks_sha256"],
        "environment": environment,
        "task_count": len(results),
        "solved": sum(result["verified_solve"] for result in results),
        "failed": sum(result["status"] == "failed" for result in results),
        "timed_out": sum(result["status"] == "timeout" for result in results),
        "results": [f"tasks/{result['task']['id']}.json" for result in results],
    }
    _atomic_json(output / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    acquire_parser = subparsers.add_parser("acquire")
    acquire_parser.add_argument("--output", type=Path, required=True)
    freeze_parser = subparsers.add_parser("freeze")
    freeze_parser.add_argument("--dataset-root", type=Path, required=True)
    freeze_parser.add_argument("--output", type=Path, default=PILOT_PATH)
    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("--search-project", type=Path, default=ROOT)
    audit_parser.add_argument("--mathlib-root", type=Path, required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--search-project", type=Path, default=ROOT)
    run_parser.add_argument("--mathlib-root", type=Path, required=True)
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--timeout", type=float, default=30.0)
    run_parser.add_argument("--limit", type=int)
    run_parser.add_argument("--search-import", default="JevLean")
    run_parser.add_argument("--tactic", default="jev_benchmark?")
    args = parser.parse_args(argv)
    try:
        if args.command == "acquire":
            print(acquire(args.output))
        elif args.command == "freeze":
            manifest = build_pilot(args.dataset_root)
            _atomic_json(args.output, manifest)
            print(manifest["tasks_sha256"])
        elif args.command == "audit":
            audit = audit_environment(args.search_project, args.mathlib_root)
            print(json.dumps(audit, indent=2, sort_keys=True))
            return 0 if audit["compatible"] else 2
        else:
            summary = run_pilot(
                args.mathlib_root, args.search_project, args.output, args.timeout,
                args.limit, args.search_import, args.tactic,
            )
            print(json.dumps(summary, indent=2, sort_keys=True))
    except (BenchmarkError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({"error": type(error).__name__, "message": str(error)}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
