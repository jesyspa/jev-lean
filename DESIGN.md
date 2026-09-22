# Current design

Use this document for the architectural boundaries of the implemented `jev?` tactic and benchmark runners. Measurements and study conclusions live in the linked reports.

## Search and trust boundary

`JevLean.lean` implements candidate generation, tactic-state branching, bounded best-first search, ranking integration, replay, and the `jev?` suggestion interface inside Lean. Lean owns goals, local contexts, action execution, automation, elaboration, and proof checking. The search catalogue contains code-rendered actions that are executed before ranking.

A deterministic scheduler owns depth, cost, transition, model-call, state-table, and wall-time ledgers. It fingerprints ordered rendered goals to suppress equal-cost duplicates and cycles. A successful path is replayed and emitted as ordinary tactic source through `Try this`.

`jevlean.model_broker` is a persistent localhost service. It owns provider credentials and separate rank and helper clients. Lean sends bounded structured requests to the broker; it never reads provider credentials. The broker does not execute Lean code.

Optional helper output is restricted to proposition and rationale text. Lean parses and elaborates each proposition in the live context. A surviving cut becomes two proof obligations: the proposed proposition from the original context, then the original goal with that proposition available. Provider output is never accepted as proof.

## User and batch interfaces

The editor-facing interface is:

```lean
theorem target ... := by
  jev?
```

Provider-dependent search belongs in an interactive or experimental run. Checked-in proofs should use the emitted ordinary Lean source.

The miniF2F and LeanDojo Benchmark 4 adapters reuse `jev_benchmark?` but add process isolation, fixed task manifests, theorem-level deadlines, and fresh source replay. They sanitize worker environments and do not pass credentials into generated-code workers. Their exact pinning and leakage controls are documented in [MINIF2F.md](MINIF2F.md) and [BENCHMARK4.md](BENCHMARK4.md).

The Pantograph code is a retained external-worker feasibility spike, not the active `jev?` search engine. Its evidence is in [PANTOGRAPH_SPIKE.md](PANTOGRAPH_SPIKE.md).

## Components

```text
Lean goal
   |
   v
checked bounded action catalogue <--- bounded Mathlib-name retrieval
   |
   v
Lean-native scheduler <--- localhost broker <--- rank/helper providers
   |
   v
fresh path replay and `Try this` source
   |
   +--- interactive suggestion
   +--- isolated benchmark replay
```

The implementation keeps one proof-search policy in `JevLean.lean`. Benchmark runners construct and isolate tasks; they do not implement a second scheduler.

## Current limits

- Tactic execution in the editor process is not hard process-isolated. The scheduler checks wall time between transitions, but an individual tactic that ignores cancellation can delay Lean.
- Retrieval uses deterministic overlap scoring and a bounded list of environment declarations. It is not a learned or Mathlib-scale premise retriever.
- Search ledgers deliberately keep the frontier small. Success on the calibration fixtures is not a broad solve-rate estimate.
- Rendered proof states and candidate descriptions leave the machine when provider-backed ranking is enabled. The README describes the transmitted fields.
- Benchmark replay checks generated source in a fresh Lean process, but compatibility depends on the benchmark's exact Lean and Mathlib revisions.
- Complete generated patches, new declarations, and arbitrary provider-generated tactics are outside the trusted action path.

## Evidence

[CALIBRATION_REPORT.md](CALIBRATION_REPORT.md) records the Lean-native bounded-search result. [REPORT.md](REPORT.md) records the earlier ranking experiments. [PANTOGRAPH_SPIKE.md](PANTOGRAPH_SPIKE.md) records external-worker feasibility and its `rw?` timeout blocker. [EXTERNAL_BENCHMARKS.md](EXTERNAL_BENCHMARKS.md) distinguishes next-action studies from end-to-end theorem benchmarks.
