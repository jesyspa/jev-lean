# Design options

This project is deciding how to build a user-facing `jev?` tactic and a headless benchmark runner. The experiments validate parts of the idea, not a complete architecture.

## Fixed responsibilities

The implementation must preserve these boundaries:

- **Lean** owns goals, local contexts, action execution, automation, and proof checking.
- **Jev** ranks bounded concrete alternatives and possible escalation routes.
- **Generative models** propose restricted direct actions or complete helper source only after ordinary search stalls.
- **Deterministic code** owns budgets, queues, caching, and traces.
- **Fresh Lean replay** accepts or rejects the final proof.

The system should reuse Lean and Mathlib facilities such as local hypothesis application, `exact?`, `apply?`, `simp?`, `aesop?`, `grind?`, rewriting, induction, arithmetic tactics, and environment lookup. It should not implement its own parser, elaborator, tactic semantics, or proof checker.

## User interface

The primary human interface should follow Lean's suggestion tactics:

```lean
theorem target ... := by
  jev?
```

On success, `jev?` closes the current goal for immediate feedback and emits a `Try this` replacement containing ordinary Lean code. Provider-dependent search should not remain in checked-in proofs.

A local command and a LeanDojo runner may invoke the same search engine for replay, CI, and batch evaluation.

## Option A: Lean-native search

Implement candidate generation and search in a Lean tactic/library using Lean's tactic-state save and restore APIs. A small local broker handles Jev and fallback-model HTTP calls without exposing credentials to Lean-generated code.

Advantages:

- direct access to the exact editor goal, expressions, local instances, and environment;
- structured use of Lean suggestion and elaboration APIs;
- no proof-state serialization or source reconstruction during tactical search;
- natural `jev?` integration.

Risks:

- a tactic that ignores cancellation can freeze the editor process;
- search-state ownership and memory use need a dedicated Lean prototype;
- arbitrary model-generated syntax cannot safely execute in the user's process;
- benchmark isolation and provider caching still need external support.

Use restricted, code-rendered action forms for direct fallback. Treat arbitrary structural patches as reviewed source changes verified in an isolated process.

## Option B: Pantograph-driven external search

Run the scheduler in an external process and use Pantograph as the interface to Lean. Pantograph uses Lean's elaborator and tactic machinery; the project adds only a thin protocol client and supervisor.

Advantages:

- hard process isolation, restart, and resource accounting;
- convenient queues, caches, provider clients, and batch concurrency;
- one controller interface for local projects and benchmarks;
- the completed spike demonstrated branching, multi-goal handling, helper isolation, recovery, and clean replay.

Risks:

- Pantograph output is not a lossless kernel-state serialization;
- editor goals must be reconstructed from source and environment state;
- measured startup and replay were expensive in the small spike;
- `rw?` exceeded the external 180-second limit despite a shorter Pantograph timeout;
- helper lineages require separate processes and prefix replay.

The `rw?` result is a capability issue, not a requirement to abandon Pantograph. A production capability manifest can omit a suggestion family only if every enabled family obeys an external wall limit and equivalent concrete rewrites remain available.

## Option C: hybrid tactic and worker

Expose `jev?` in the editor but run search in an isolated Lean worker, implemented either as a Lean executable or through Pantograph. Return checked proof syntax to the original Lean process for a second elaboration.

This preserves the preferred UI and process isolation. It retains the main external design cost: faithfully reconstructing the current goal and environment in the worker.

## Shared components

```text
project/benchmark task
        |
        v
Lean execution and action generation <--- premise index
        |
        v
budgeted search <--- Jev adapter <--- credential-holding model broker
        |
        +---------- direct fallback
        +---------- optional complete helper patches
        |
        v
fresh replay and dependency audit
        |
        +---------- `jev?` suggestion
        +---------- benchmark result
```

Required components are:

1. task construction that excludes the target body and unavailable later declarations;
2. Lean-backed dynamic action generation and bounded premise retrieval;
3. a theorem-level budget ledger and deterministic search scheduler;
4. safe Jev request, cache, and trace handling;
5. restricted direct fallback;
6. fresh source replay and dependency audit;
7. a `jev?` front end and resumable benchmark runner;
8. later, isolated complete lemma, generalization, and definition patches.

The user tactic and benchmark runner should contain no separate proof-search policy.

## Decision needed before implementation

The Pantograph spike tested Option B. The comparable next step is a small Option A spike, not a full prover. It should measure:

- branching and restoring several Lean tactic states;
- structured extraction of Lean suggestions;
- cancellation of a deliberately expensive tactic;
- memory growth across a bounded frontier;
- generation of a normal `Try this` replacement;
- headless execution of the same search entry point.

Choose Lean-native, Pantograph, or hybrid only after comparing those results with the committed Pantograph measurements. The decision should prioritize correctness of state handling, reliable cancellation, interactive latency, and implementation complexity rather than language preference.

## Evaluation sequence

After selecting the execution design:

1. build a deterministic catalogue-only prover;
2. compare Jev and deterministic scheduling on identical verified successor sets;
3. add direct generative fallback;
4. run a compatible LeanDojo `novel_premises` sample;
5. add and evaluate complete helper patches on induction-heavy tasks.

Report verified solve rate together with Lean CPU, wall time, expanded nodes, Jev calls, LLM calls, tokens, and cost. Classify failures as unknown when no audit demonstrates generation, retrieval, ranking, or budget causation.
