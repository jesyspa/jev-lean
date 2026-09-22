"""Bounded Pantograph feasibility spike.

This is deliberately a test harness, not a proof-search controller.  It keeps
runtime state handles process-local and reconstructs source lineages after a
worker is lost.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import benchmark4

PANTOGRAPH_COMMIT = "7076ab3632b5de67a4f83ab259b23b37acaea1d0"
LEAN_VERSION = "4.30.0"
ROOT = Path(__file__).resolve().parents[1]
REPL = ROOT / ".lake/packages/pantograph/.lake/build/bin/repl"


class PantographError(RuntimeError):
    pass


class MeasuredBlocker(PantographError):
    def __init__(self, message: str, evidence: dict[str, Any]):
        super().__init__(message)
        self.evidence = evidence


@dataclass
class Measurement:
    status: str
    evidence: dict[str, Any]


@dataclass
class Profile:
    samples_ms: dict[str, list[float]] = field(default_factory=dict)

    def add(self, family: str, seconds: float) -> None:
        self.samples_ms.setdefault(family, []).append(round(seconds * 1000, 3))

    def summary(self) -> dict[str, dict[str, float | int]]:
        result: dict[str, dict[str, float | int]] = {}
        for family, values in sorted(self.samples_ms.items()):
            ordered = sorted(values)
            result[family] = {
                "n": len(values),
                "min_ms": round(ordered[0], 3),
                "median_ms": round(statistics.median(ordered), 3),
                "max_ms": round(ordered[-1], 3),
            }
        return result


def _runtime_environment() -> tuple[Path, str]:
    try:
        return benchmark4._search_environment(str(ROOT.resolve()))
    except benchmark4.BenchmarkError as error:
        raise PantographError(str(error)) from error


def _sandbox_prefix(writable: Path | None = None) -> list[str]:
    """Return a credential-free, network-free bubblewrap prefix.

    Only the project, Lean toolchain, and shared Lean cache are visible.  The
    project and toolchain mounts are read-only.  A caller may expose one
    isolated writable directory as /workspace.
    """

    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise PantographError("bubblewrap (bwrap) is required for isolated Pantograph execution")
    lean, lean_path = _runtime_environment()
    command = [bwrap, "--die-with-parent", "--new-session", "--unshare-net"]
    for host_path in (Path("/usr"), Path("/lib"), Path("/lib64")):
        if host_path.exists():
            command += ["--ro-bind", str(host_path), str(host_path)]
    command += ["--dir", "/etc"]
    for host_path in (Path("/etc/ld.so.cache"), Path("/etc/localtime")):
        if host_path.exists():
            command += ["--ro-bind", str(host_path), str(host_path)]
    command += ["--tmpfs", "/home", "--tmpfs", "/tmp"]
    try:
        command += benchmark4._readonly_mounts(ROOT)
        command += benchmark4._runtime_mounts(lean, lean_path)
        if REPL.exists():
            command += benchmark4._readonly_mounts(REPL)
    except benchmark4.BenchmarkError as error:
        raise PantographError(str(error)) from error
    command += ["--proc", "/proc", "--dev", "/dev", "--dir", "/tmp/home"]
    if writable is not None:
        command += ["--dir", "/workspace", "--bind", str(writable), "/workspace"]
    command += [
        "--chdir", str(ROOT),
        "--setenv", "HOME", "/tmp/home",
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "LEAN_PATH", lean_path,
        "--setenv", "PATH", f"{lean.parents[1]}/bin:/usr/bin:/bin",
        "--setenv", "LANG", "C.UTF-8",
    ]
    return command


class PantographWorker:
    """Small JSON-lines adapter around the pinned Pantograph REPL."""

    def __init__(self, imports: tuple[str, ...] = ("Init",), startup_timeout: float = 90):
        if not REPL.is_file():
            raise PantographError(
                "Pantograph REPL is absent; build it with `lake build @pantograph/repl`"
            )
        started = time.monotonic()
        command = _sandbox_prefix() + [str(REPL), *imports]
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/tmp",
            "LANG": "C.UTF-8",
        }
        self.process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        assert self.process.stdout is not None
        line = self._readline(startup_timeout)
        self.startup_seconds = time.monotonic() - started
        if line.strip() != "ready.":
            self.close()
            raise PantographError(f"Pantograph did not become ready: {line!r}")

    def _readline(self, timeout: float) -> str:
        assert self.process.stdout is not None
        # communicate() cannot be used because the worker is persistent.  poll()
        # plus a short selector wait keeps failures bounded.
        import selectors

        selector = selectors.DefaultSelector()
        selector.register(self.process.stdout, selectors.EVENT_READ)
        events = selector.select(timeout)
        selector.close()
        if not events:
            self.close()
            raise PantographError(f"Pantograph response exceeded {timeout:.1f}s")
        line = self.process.stdout.readline()
        if not line:
            stderr = ""
            if self.process.stderr is not None:
                stderr = self.process.stderr.read()
            raise PantographError(f"Pantograph exited unexpectedly: {stderr[-2000:]}")
        return line

    def command(self, command: str, payload: dict[str, Any], timeout: float = 180) -> dict[str, Any]:
        if self.process.poll() is not None:
            raise PantographError("Pantograph worker is not running")
        assert self.process.stdin is not None
        wire = f"{command} {json.dumps(payload, separators=(',', ':'))}\n"
        self.process.stdin.write(wire)
        self.process.stdin.flush()
        result = json.loads(self._readline(timeout))
        return result

    def close(self) -> None:
        process = getattr(self, "process", None)
        if process is None:
            return
        try:
            if process.poll() is None:
                try:
                    assert process.stdin is not None
                    process.stdin.write("\n")
                    process.stdin.flush()
                    process.wait(timeout=3)
                except (BrokenPipeError, subprocess.TimeoutExpired):
                    process.kill()
                    process.wait(timeout=5)
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()

    def __enter__(self) -> "PantographWorker":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _timed(profile: Profile, family: str, action: Callable[[], Any]) -> Any:
    started = time.monotonic()
    try:
        return action()
    finally:
        profile.add(family, time.monotonic() - started)


def _expect_success(result: dict[str, Any], context: str) -> dict[str, Any]:
    if "error" in result or "nextStateId" not in result or "goals" not in result:
        raise PantographError(f"{context} failed: {json.dumps(result, sort_keys=True)}")
    if result.get("hasSorry") or result.get("hasUnsafe"):
        raise PantographError(f"{context} used sorry or unsafe code")
    return result


def _start(worker: PantographWorker, expression: str) -> int:
    result = worker.command("goal.start", {"expr": expression})
    if "stateId" not in result:
        raise PantographError(f"goal.start failed: {result}")
    return int(result["stateId"])


def _tactic(
    worker: PantographWorker,
    state: int,
    tactic: str,
    goal_id: int = 0,
    *,
    timeout: float = 180,
) -> dict[str, Any]:
    return worker.command(
        "goal.tactic",
        {"stateId": state, "goalId": goal_id, "autoResume": True, "tactic": tactic},
        timeout=timeout,
    )


def materialize_suggestions(messages: list[dict[str, Any]]) -> list[str]:
    """Extract concrete tactics from Pantograph's serialized `Try this` text.

    Pantograph does not expose Lean code actions structurally at this pin.  The
    adapter therefore accepts only the explicit `[apply]` replacement lines and
    never treats the discovery state's admitted goals as successors.
    """

    suggestions: list[str] = []
    for message in messages:
        data = str(message.get("data", ""))
        lines = data.splitlines()
        for index, line in enumerate(lines):
            if "[apply]" not in line:
                continue
            candidate = line.split("[apply]", 1)[1].strip()
            if candidate:
                suggestions.append(candidate)
                continue
            # Defensive support for a renderer that puts code on the next line.
            for continuation in lines[index + 1 :]:
                if continuation.strip() and not continuation.lstrip().startswith("--"):
                    suggestions.append(continuation.strip())
                    break
    return list(dict.fromkeys(suggestions))


def _frontend(worker: PantographWorker, source: str, *, inherit: bool = True,
              new_constants: bool = False, read_header: bool = False) -> dict[str, Any]:
    result = worker.command(
        "frontend.process",
        {
            "file": source,
            "readHeader": read_header,
            "inheritEnv": inherit,
            "newConstants": new_constants,
        },
        timeout=180,
    )
    if "error" in result:
        raise PantographError(f"frontend.process failed: {result}")
    errors = [
        message
        for unit in result.get("units", [])
        for message in unit.get("messages", [])
        if message.get("severity") == "error"
    ]
    if errors:
        raise PantographError(f"frontend errors: {errors}")
    return result


def _initialize_lineage(helper_source: str, target: str, prefix: tuple[str, ...]) -> tuple[PantographWorker, int]:
    worker = PantographWorker(("Init",))
    try:
        _frontend(worker, helper_source, inherit=True)
        state = _start(worker, target)
        for tactic in prefix:
            result = _expect_success(_tactic(worker, state, tactic), f"replay {tactic}")
            state = int(result["nextStateId"])
        return worker, state
    except Exception:
        worker.close()
        raise


def replay_source(source: str, timeout: float = 120) -> dict[str, Any]:
    """Elaborate source in a fresh, network-free process with a hidden home."""

    with tempfile.TemporaryDirectory(prefix="pantograph-replay-") as directory:
        writable = Path(directory)
        (writable / "Replay.lean").write_text(source, encoding="utf-8")
        lean, _ = _runtime_environment()
        command = _sandbox_prefix(writable) + [str(lean), "/workspace/Replay.lean"]
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8"},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        return {"accepted": completed.returncode == 0, "returncode": completed.returncode,
                "output": completed.stdout[-4000:]}


@dataclass(frozen=True)
class HoleExtraction:
    prefix: str
    target_command: str
    prefix_sha256: str
    target_boundary: tuple[int, int]


def extract_theorem_hole(worker: PantographWorker, source: str, target_name: str) -> HoleExtraction:
    """Use Lean command boundaries to cut before the target and all later code.

    Pantograph cannot safely run newly imported module initializers after REPL
    startup.  The bounded adapter therefore requires a contiguous import header,
    launches the worker with those exact modules, and processes only the body in
    the already initialized environment.
    """

    header_match = re.match(r"(?:(?:prelude\s*\n)?(?:import\s+[^\n]+\n)+)", source)
    header_end = header_match.end() if header_match else 0
    body = source[header_end:]
    result = _frontend(worker, body, inherit=False, new_constants=True, read_header=False)
    candidates: list[tuple[int, int]] = []
    for unit in result.get("units", []):
        constants = unit.get("newConstants", []) or []
        if target_name in constants:
            local_start, local_end = unit["boundary"]
            start = len(source[:header_end].encode("utf-8")) + int(local_start)
            end = len(source[:header_end].encode("utf-8")) + int(local_end)
            candidates.append((start, end))
    if len(candidates) != 1:
        raise PantographError(f"expected one command defining {target_name}, found {candidates}")
    start, end = candidates[0]
    prefix = source.encode("utf-8")[:start].decode("utf-8")
    target = source.encode("utf-8")[start:end].decode("utf-8")
    return HoleExtraction(
        prefix=prefix,
        target_command=target,
        prefix_sha256=hashlib.sha256(prefix.encode()).hexdigest(),
        target_boundary=(start, end),
    )


class SpikeRunner:
    def __init__(self) -> None:
        self.profile = Profile()
        self.gates: dict[str, Measurement] = {}

    def record(self, name: str, test: Callable[[], dict[str, Any]]) -> None:
        try:
            evidence = test()
            self.gates[name] = Measurement("pass", evidence)
        except Exception as exc:  # Every unsupported behavior remains visible in the artifact.
            evidence = {"type": type(exc).__name__, "message": str(exc)}
            evidence.update(getattr(exc, "evidence", {}))
            self.gates[name] = Measurement("blocker", evidence)

    def run(self) -> dict[str, Any]:
        manifest = json.loads((ROOT / "lake-manifest.json").read_text())
        pantograph = next((p for p in manifest["packages"] if p["name"] == "pantograph"), None)
        if pantograph is None or pantograph.get("rev") != PANTOGRAPH_COMMIT:
            raise PantographError("lake manifest does not contain the required Pantograph commit")
        lean_executable, _ = _runtime_environment()
        lean = subprocess.run(
            [str(lean_executable), "--version"], text=True,
            stdout=subprocess.PIPE, check=True, timeout=10,
        ).stdout.strip()
        if f"version {LEAN_VERSION}" not in lean:
            raise PantographError(f"unexpected Lean version: {lean}")

        self.record("immutable_branching", self.immutable_branching)
        self.record("fixed_goal_auto_resume", self.fixed_goal_auto_resume)
        self.record("root_unresolved_metavariable", self.root_unresolved_metavariable)
        self.record("independent_suggestions", self.independent_suggestions)
        self.record("isolated_helper_lineages", self.isolated_helper_lineages)
        self.record("helper_namespace_instance_attribute", self.helper_namespace_instance_attribute)
        self.record("frontier_recovery", self.frontier_recovery)
        self.record("clean_source_replay", self.clean_source_replay)
        self.record("safe_theorem_hole_extraction", self.safe_theorem_hole_extraction)
        self.record("latency_profile", self.latency_profile)
        passed = sum(g.status == "pass" for g in self.gates.values())
        return {
            "schema": 1,
            "pantograph_commit": PANTOGRAPH_COMMIT,
            "lean_version": LEAN_VERSION,
            "sandbox": "bubblewrap, network unshared, hidden home, read-only project/toolchain",
            "auto_resume": True,
            "gates": {name: {"status": gate.status, "evidence": gate.evidence}
                      for name, gate in self.gates.items()},
            "summary": {"pass": passed, "blocker": len(self.gates) - passed, "total": len(self.gates)},
            "latency": {"samples_ms": self.profile.samples_ms, "summary": self.profile.summary()},
        }

    def _worker(self, imports: tuple[str, ...] = ("Init",)) -> PantographWorker:
        worker = PantographWorker(imports)
        self.profile.add("startup", worker.startup_seconds)
        worker.command("options.set", {"automaticMode": True, "timeout": 180000})
        return worker

    def immutable_branching(self) -> dict[str, Any]:
        with self._worker() as worker:
            ancestor = _start(worker, "∀ p q : Prop, p → q → p ∧ q")
            opened = _expect_success(_tactic(worker, ancestor, "intro p q hp hq"), "intro")
            ancestor = int(opened["nextStateId"])
            left = _expect_success(_tactic(worker, ancestor, "exact And.intro hp hq"), "left branch")
            right = _expect_success(_tactic(worker, ancestor, "constructor"), "right branch")
            original = worker.command("goal.print", {"stateId": ancestor, "goals": True})
            if len(original.get("goals", [])) != 1 or left["nextStateId"] == right["nextStateId"]:
                raise PantographError("ancestor changed or child state IDs collided")
            return {"ancestor": ancestor, "children": [left["nextStateId"], right["nextStateId"]],
                    "ancestor_goals": len(original["goals"])}

    def fixed_goal_auto_resume(self) -> dict[str, Any]:
        with self._worker() as worker:
            state = _start(worker, "∀ p q : Prop, p → q → p ∧ q")
            state = int(_expect_success(_tactic(worker, state, "intro p q hp hq"), "intro")["nextStateId"])
            split = _expect_success(_tactic(worker, state, "constructor"), "constructor")
            state = int(split["nextStateId"])
            before = [g["target"]["pp"] for g in split["goals"]]
            focused = _expect_success(_tactic(worker, state, "exact hq", goal_id=1), "focused second goal")
            after = [g["target"]["pp"] for g in focused["goals"]]
            if before != ["p", "q"] or after != ["p"]:
                raise PantographError(f"fixed focus did not preserve the other goal: {before} -> {after}")
            return {"goal_id": 1, "auto_resume": True, "before": before, "after": after}

    def root_unresolved_metavariable(self) -> dict[str, Any]:
        with self._worker() as worker:
            state = _start(worker, "∀ p q : Prop, p → q → p ∧ q")
            state = int(_expect_success(_tactic(worker, state, "intro p q hp hq"), "intro")["nextStateId"])
            # Construct two goals with manual resumption, then close only the
            # visible one.  The dormant sibling gives a supported lost-visible-
            # goal fixture without injecting a custom metaprogram.
            split = worker.command(
                "goal.tactic",
                {"stateId": state, "goalId": 0, "autoResume": False, "tactic": "constructor"},
            )
            split = _expect_success(split, "manual split")
            state = int(split["nextStateId"])
            closed = worker.command(
                "goal.tactic",
                {"stateId": state, "goalId": 0, "autoResume": False, "tactic": "exact hp"},
            )
            closed = _expect_success(closed, "close visible goal")
            state = int(closed["nextStateId"])
            root = worker.command("goal.print", {"stateId": state, "rootExpr": True, "goals": True})
            if root.get("goals") != [] or root.get("rootHasMVar") is not True:
                raise PantographError(f"root inspection missed unresolved metavariable: {root}")
            return {"fixture_auto_resume": False, "experiment_auto_resume": True,
                    "visible_goals": 0, "rootHasMVar": True,
                    "root": root.get("root", {}).get("pp")}

    def independent_suggestions(self) -> dict[str, Any]:
        groups = [
            (("Init",), [
                ("exact?", "∀ p : Prop, p → p", "intro p h", "suggestion"),
                ("apply?", "∀ p q : Prop, (p → q) → p → q", "intro p q h hp", "suggestion"),
                ("simp?", "∀ n : Nat, n + 0 = n", "intro n", "suggestion"),
                ("rw?", "∀ p q : Prop, p → p ∨ q", "intro p q h", "suggestion"),
            ]),
            (("Mathlib.Tactic",), [
                ("aesop?", "∀ p q : Prop, p → p ∨ q", "intro p q h", "broad_automation"),
                ("grind? +suggestions", "∀ a b : Nat, a = b → a + 1 = b + 1", "intro a b h", "broad_automation"),
            ]),
        ]
        evidence: dict[str, Any] = {}
        failures: list[str] = []
        for imports, cases in groups:
            current_family = "startup"
            try:
                with self._worker(imports) as worker:
                    for family, expression, setup, profile_family in cases:
                        current_family = family
                        root = _start(worker, expression)
                        ancestor_result = _expect_success(_tactic(worker, root, setup), f"setup {family}")
                        ancestor = int(ancestor_result["nextStateId"])
                        if family == "rw?":
                            # This command does not check cancellation promptly at
                            # the pin.  A 10 s Pantograph timeout bounds its search
                            # request, while the outer 180 s wall bound measures
                            # the delayed response honestly.
                            worker.command("options.set", {"timeout": 10000})
                        discovery = _timed(
                            self.profile, profile_family,
                            lambda f=family, s=ancestor: _tactic(worker, s, f, timeout=180),
                        )
                        if family == "rw?":
                            worker.command("options.set", {"timeout": 180000})
                        suggestions = materialize_suggestions(discovery.get("messages", []))
                        if not suggestions:
                            failures.append(f"{family}: no materializable suggestion: {discovery}")
                            continue
                        candidate = suggestions[0]
                        execution = _timed(
                            self.profile, "ordinary_tactic",
                            lambda s=ancestor, c=candidate: _tactic(worker, s, c, timeout=120),
                        )
                        try:
                            execution = _expect_success(execution, f"materialized {family} candidate")
                        except PantographError as exc:
                            failures.append(str(exc))
                            continue
                        ancestor_after = worker.command("goal.print", {"stateId": ancestor, "goals": True})
                        if len(ancestor_after.get("goals", [])) != len(ancestor_result["goals"]):
                            failures.append(f"{family}: discovery mutated its ancestor")
                            continue
                        evidence[family] = {
                            "candidate": candidate,
                            "result_goals": len(execution["goals"]),
                            "discovery_state_retained": False,
                        }
            except PantographError as exc:
                failures.append(f"{current_family} with imports {imports}: {exc}")
        if failures:
            raise MeasuredBlocker(
                "; ".join(failures),
                {"completed_families": evidence, "failures": failures},
            )
        return evidence

    def isolated_helper_lineages(self) -> dict[str, Any]:
        target = "∀ n : Nat, n + 0 = n"
        prefix = ("intro n",)
        source_a = "namespace Generated.A\ntheorem finish (n : Nat) : n + 0 = n := Nat.add_zero n\nend Generated.A"
        source_b = "namespace Generated.B\ntheorem finish (n : Nat) : n + 0 = n := Nat.add_zero n\nend Generated.B"
        a, state_a = _initialize_lineage(source_a, target, prefix)
        b, state_b = _initialize_lineage(source_b, target, prefix)
        try:
            # Alternate workers, and require inaccessible foreign declarations.
            solved_a = _expect_success(_tactic(a, state_a, "exact Generated.A.finish n"), "lineage A")
            leak_b = _tactic(b, state_b, "exact Generated.A.finish n")
            solved_b = _expect_success(_tactic(b, state_b, "exact Generated.B.finish n"), "lineage B")
            leak_a = _tactic(a, state_a, "exact Generated.B.finish n")
            if solved_a["goals"] or solved_b["goals"]:
                raise PantographError("a helper lineage did not solve its target")
            if "nextStateId" in leak_a or "nextStateId" in leak_b:
                raise PantographError("helper declaration leaked across isolated lineages")
            return {"base_prefix_depth": 1, "lineages": 2, "alternated": True,
                    "cross_lineage_attempts_rejected": 2}
        finally:
            a.close()
            b.close()

    def helper_namespace_instance_attribute(self) -> dict[str, Any]:
        helper = """namespace Generated.Scoped
class Tagged (n : Nat) : Prop where proof : n = n
local instance (n : Nat) : Tagged n := ⟨rfl⟩
theorem viaInstance (n : Nat) : n = n := Tagged.proof
@[irreducible] def wrapped (n : Nat) := n
@[simp] theorem wrapped_eq (n : Nat) : wrapped n = n := by rw [wrapped]
end Generated.Scoped"""
        target = "∀ n : Nat, n = n ∧ Generated.Scoped.wrapped n = n"
        worker, state = _initialize_lineage(helper, target, ("intro n",))
        try:
            result = _expect_success(
                _tactic(worker, state, "exact ⟨Generated.Scoped.viaInstance n, by simp⟩"),
                "scoped helper action",
            )
            if result["goals"]:
                raise PantographError("namespace/instance/attribute helper left goals")
            return {"namespace": "Generated.Scoped", "local_instance_compiled": True,
                    "simp_attribute_used": True, "remaining_goals": 0}
        finally:
            worker.close()

    def frontier_recovery(self) -> dict[str, Any]:
        target = "∀ p q : Prop, p → q → p ∧ q"
        prefixes = [
            ("intro p q hp hq",),
            ("intro p q hp hq", "constructor"),
            ("intro p q hp hq", "refine ⟨hp, ?_⟩"),
        ]
        with self._worker() as dead:
            live_ids = []
            for prefix in prefixes:
                state = _start(dead, target)
                for tactic in prefix:
                    state = int(_expect_success(_tactic(dead, state, tactic), "frontier creation")["nextStateId"])
                live_ids.append(state)
        # The original process is now terminated.  Reconstruct all records in one
        # replacement generation, from source tactics rather than stale state IDs.
        started = time.monotonic()
        with self._worker() as replacement:
            recovered = []
            for prefix in prefixes:
                state = _start(replacement, target)
                for tactic in prefix:
                    state = int(_expect_success(_tactic(replacement, state, tactic), "frontier replay")["nextStateId"])
                printed = replacement.command("goal.print", {"stateId": state, "goals": True})
                recovered.append([goal["target"]["pp"] for goal in printed["goals"]])
        self.profile.add("recovery", time.monotonic() - started)
        if len(recovered) != 3 or any(not goals for goals in recovered):
            raise PantographError(f"frontier reconstruction failed: {recovered}")
        return {"invalidated_runtime_ids": live_ids, "reconstructed": len(recovered),
                "goal_targets": recovered}

    def clean_source_replay(self) -> dict[str, Any]:
        source = """import Mathlib.Tactic
namespace Generated.Replay
private theorem helper (n : Nat) : n + 0 = n := by simp
theorem target (n : Nat) : n + 0 = n := helper n
end Generated.Replay
"""
        result = _timed(self.profile, "replay", lambda: replay_source(source))
        if not result["accepted"] or "sorry" in result["output"].lower():
            raise PantographError(f"clean replay rejected source: {result}")
        return {"fresh_process": True, "accepted": True, "output": result["output"]}

    def safe_theorem_hole_extraction(self) -> dict[str, Any]:
        source = """import Init
namespace ExtractionFixture
def prior : Prop := True
theorem target : prior := True.intro
theorem later : True := True.intro
end ExtractionFixture
"""
        with self._worker(("Init",)) as worker:
            extraction = extract_theorem_hole(worker, source, "ExtractionFixture.target")
        if "target" in extraction.prefix or "later" in extraction.prefix:
            raise PantographError("target or later declaration leaked into source prefix")
        visible = replay_source(extraction.prefix + "\n#check ExtractionFixture.prior\n")
        target_probe = replay_source(extraction.prefix + "\n#check ExtractionFixture.target\n")
        later_probe = replay_source(extraction.prefix + "\n#check ExtractionFixture.later\n")
        if not visible["accepted"] or target_probe["accepted"] or later_probe["accepted"]:
            raise PantographError(
                f"extracted visibility is unsafe: prior={visible}, target={target_probe}, later={later_probe}"
            )
        return {"boundary": list(extraction.target_boundary),
                "prefix_sha256": extraction.prefix_sha256,
                "prior_visible": True, "target_visible": False, "later_visible": False}

    def latency_profile(self) -> dict[str, Any]:
        required = {
            "startup", "suggestion", "ordinary_tactic", "broad_automation",
            "helper_compilation", "recovery", "replay",
        }
        # Helper compilation was exercised by both helper gates; record a direct,
        # bounded sample here so all requested families have explicit timings.
        with self._worker() as worker:
            _timed(
                self.profile,
                "helper_compilation",
                lambda: _frontend(worker, "theorem Generated.profileHelper : True := True.intro"),
            )
        missing = required - self.profile.samples_ms.keys()
        if missing:
            raise PantographError(f"missing latency families: {sorted(missing)}")
        return {"families": sorted(required), "clock": "time.monotonic", "unit": "milliseconds"}


def run_and_write(path: Path | None) -> dict[str, Any]:
    result = SpikeRunner().run()
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = run_and_write(args.output)
    print(json.dumps(result["summary"], sort_keys=True))
    return 0 if result["summary"]["blocker"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
