# Pantograph feasibility spike

## Verdict

The retained bounded spike records 9 passing gates and 1 blocker. It evaluates an external-worker design; the active `jev?` search is Lean-native and does not depend on this spike at runtime.

Pantograph is pinned to `7076ab3632b5de67a4f83ab259b23b37acaea1d0` under Lean 4.30.0. Immutable branching, fixed focused multi-goal execution, root metavariable inspection, isolated helper lineages, scoped helper declarations, frontier reconstruction, clean replay, theorem-hole extraction, and latency collection passed.

The independent-suggestion gate is blocked by `rw?`. `exact?`, `apply?`, `simp?`, `aesop?`, and `grind? +suggestions` each emitted a concrete replacement that succeeded when executed from its untouched ancestor. `rw?` did not return within the 180-second process wall limit, even with Pantograph's tactic timeout set to 10 seconds. The wall limit killed the credential-free worker, so the harness records a blocker rather than a synthetic suggestion.

The source-level lineage adapter is thin enough for the passing helper and recovery gates. It reconstructs each helper environment in a separate Pantograph process and replays tactic prefixes. It never equates states by pretty output.

## Reproduction

Run the spike and the full credential-free suite from the repository root:

```bash
RUN_PANTOGRAPH_SPIKE=1 ./test.sh
```

The default `./test.sh` skips this slow integration run. With the flag set, the harness builds Pantograph, writes new spike output to a temporary file, and checks the recorded gates without modifying `artifacts/pantograph-spike-results.json`. Feasibility blockers are asserted as measured outcomes, so the suite can pass while the artifact carries the gate verdict.

## Recorded gates

| Gate | Result | Evidence |
|---|---:|---|
| Immutable branching | Pass | Two child state IDs from one ancestor; ancestor retained one goal. |
| Fixed `goalId` and `autoResume` | Pass | Focusing goal 1 changed `[p, q]` to `[p]`. |
| Root unresolved metavariable | Pass | No visible goals; `rootHasMVar = true`. |
| Independent suggestions | Blocker | Five families materialized and executed; `rw?` exceeded 180 seconds. |
| Isolated helper lineages | Pass | Two lineages alternated; two cross-lineage references were rejected. |
| Namespace, instance, attribute helpers | Pass | Namespaced declarations, a local instance proof, and a simp-tagged lemma compiled and solved the target. |
| Frontier recovery | Pass | Three live branches were reconstructed after worker termination. |
| Clean source replay | Pass | A fresh sandboxed Lean process accepted the helper and theorem source. |
| Safe theorem-hole extraction | Pass | The prior declaration remained visible; target and later declarations were unavailable. |
| Latency profile | Pass | All requested operation families have monotonic-clock samples. |

## Recorded latency

The committed artifact is `artifacts/pantograph-spike-results.json`. Its measured medians were:

| Operation | Samples | Median |
|---|---:|---:|
| Startup | 9 | 15.042 s |
| Suggestions, including timed-out `rw?` | 4 | 2.001 s |
| Ordinary tactics | 5 | 5.824 ms |
| Broad automation | 2 | 3.319 s |
| Helper compilation | 1 | 190.601 ms |
| Recovery | 1 | 18.084 s |
| Clean replay | 1 | 39.634 s |

The distributions are small feasibility samples, not performance estimates. The artifact includes every sample and min/median/max summaries.

## Isolation boundary

`jevlean.pantograph_spike` launches generated Lean execution through Bubblewrap. The worker has no network, receives a hidden home directory and sanitized environment, and sees only read-only project, toolchain, and shared Lean-cache mounts plus an isolated writable replay directory. Provider credentials are never passed to the worker.
