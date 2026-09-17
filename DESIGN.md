# Jev-first Lean prover implementation proposal

Use this proposal to build and evaluate an end-to-end prover. Begin with the bounded backend spike. Start the controller only after that spike passes its gate.

## Goal

The prover accepts a theorem declaration with its proof removed and returns either:

- a proof and optional private helper declarations accepted by clean Lean elaboration; or
- a structured failure record with the remaining frontier and evidence-backed failure causes.

The controller searches Lean-verified states. Jev ranks promising paths. A generative LLM supplies direct actions or complete helper patches when ordinary Lean actions stall.

Measure verified solve rate against total Lean CPU, wall time, Jev calls, and generative-model tokens. Report all tasks and automation-resistant tasks separately.

## Feasibility gate

The first deliverable is a Pantograph spike, not the full controller. Pin Pantograph to commit `7076ab3632b5de67a4f83ab259b23b37acaea1d0` and Lean 4.30.

The spike passes only when automated tests demonstrate:

1. immutable branching from an ancestor `stateId`;
2. focused tactic execution with a fixed `goalId` and `autoResume` policy while preserving other goals;
3. root-level detection of unresolved metavariables after visible goals disappear;
4. extraction and independent execution of concrete `exact?`, `apply?`, `rw?`, `simp?`, `aesop?`, and `grind? +suggestions` suggestions;
5. two isolated helper lineages created from one non-root state, alternating execution without helper leakage;
6. helper behavior under namespaces, local instances, and attributes;
7. worker termination with several live branches, followed by reconstruction of every retained branch;
8. clean source replay in a fresh process;
9. theorem-hole extraction that prevents access to the target declaration and later declarations;
10. latency profiles for startup, suggestions, ordinary tactics, broad automation, helper compilation, recovery, and replay.

Pantograph goal output is not a lossless kernel-state serialization. The spike uses conservative state identity. It does not prune two states merely because normalized pretty output or expression S-expressions match.

If helper isolation, branch reconstruction, or root completion cannot be made reliable with a thin adapter, evaluate a project-owned Lean worker before implementing search.

## System boundary

The controller is an external Python process. It owns search, budgets, provider calls, caches, and traces. A credential-free supervisor owns Pantograph. Lean is the correctness oracle. Jev supplies bounded preferences. A generative LLM writes candidate Lean source.

```text
CLI / benchmark runner
        |
        v
Python controller
  |        |             |
  |        |             +-- LLM providers: actions and complete helper patches
  |        +---------------- Jev: sibling preference, route, path estimate
  +------------------------- sandboxed Pantograph supervisor
                                  |
                                  +-- Lean/Mathlib automation and suggestions
                                  +-- tactic execution and branching state handles
                                  +-- isolated helper lineages
                                  +-- clean source replay
```

Generated Lean code executes in a sandbox with no credentials, no network, read-only project and toolchain mounts, an isolated writable directory, and process-tree CPU, memory, PID, output, and wall-time limits. Provider access stays in the controller. Lean verification does not replace this sandbox because tactics and elaborators can run metaprograms with IO.

Use a strict trust profile initially. Reject `sorry`, `admit`, new axioms, unsafe dependencies, and native-evaluation evidence. Exclude `native_decide`, whose Lean 4.30 implementation expands the trusted base through native evaluation. Audit transitive proof dependencies in clean replay.

## Backend interface

Implement this interface after the spike fixes the concrete handle semantics:

```python
initialize(task) -> ProofSession
suggest(state_handle, family, limit) -> list[Action]
execute(state_handle, goal_id, action, timeout) -> Invalid | Solved | Successor
compile_patch(task, verified_prefix, patch, timeout) -> Invalid | PatchSuccessor
reconstruct(replay_prefix, lineage) -> StateHandle
inspect_root(state_handle) -> RootStatus
replay(task, proof, declarations, timeout) -> ReplayResult
close(session) -> None
```

`StateHandle` contains the Pantograph process generation, executable `stateId`, selected `goalId`, complete visible goal list, replay prefix, environment lineage, and an advisory fingerprint. Runtime handles never enter persistent transition-cache keys.

Fix `autoResume` for the whole experiment. Actions focus the selected goal and retain all other goals. A state is solved only when root inspection finds no unresolved metavariables and clean replay accepts the complete source.

Suggestion tactics are discovery operations. Parse their messages, materialize concrete replacements, and execute each replacement independently from the ancestor state. Never retain a state produced by a suggestion command itself; Lean's suggestion machinery may temporarily admit goals while reporting candidates.

A worker crash invalidates every runtime state handle. Reconstruct retained frontier nodes lazily from their source prefixes and environment lineages.

## Theorem-hole environment

Construct each task from its source prefix and exact imports. Remove the target body before initialization. Exclude the target declaration and unavailable later declarations from both automation and retrieval.

Freeze and record:

- repository and commit;
- Lean toolchain and Lake manifest;
- source path and prefix hash;
- imports and namespace/options state;
- target statement;
- admissible declarations;
- extraction procedure and exclusions.

Reference proofs, recorded continuations, and test answers remain outside prompts and retrieval indexes.

## Reuse Lean and Mathlib automation

Lean and Mathlib generate actions. The controller allocates and ranks their results.

The Lean 4.30 capability set includes:

- local closure and application through `assumption`, concrete `exact`, `apply`, `refine`, and `solve_by_elim` actions;
- rewriting and simplification through both directions of local equalities, `rw?`, `simp?`, `simp`, and `simpa`;
- environment suggestions through `exact?`, `apply?`, all-suggestions variants, and `Lean.LibrarySuggestions.select`;
- structure through `intro`, `constructor`, `left`, `right`, `use`, `ext`, `funext`, `cases`, and `induction`;
- bounded automation through `try?`, `aesop`, `aesop?`, `grind`, `grind? +suggestions`, `tauto`, and `contradiction`;
- strict-profile arithmetic and decision procedures through `decide`, `omega`, `norm_num`, `linarith`, `nlinarith`, and `ring`.

`library_search` is not an independent supported action at this pin. Use its supported suggestion successors and a separate retriever.

The initial retriever indexes every admissible declaration name and type expression. It performs goal-head/type-shape filtering, token/name retrieval, and Lean elaboration of concrete `exact`, `apply`, `rw`, and `simpa using` forms. Widen from 8 to 32 retrieved declarations only after the first batch yields no retained transition. Measure full-index recall and latency.

Charge suggestion discovery, retrieval, and independent candidate execution to the theorem ledger.

## Search model

Search complete multi-goal Lean states as an ordinary best-first graph. Each edge is an alternative action or complete helper patch. A node succeeds only when all goals in its Lean state are closed. This gives conjunctive completion without pretending that goals can be solved independently when metavariables or local contexts couple them.

Each frontier record stores:

- executable or reconstructible state handle;
- all goals and selected goal;
- verified source prefix and helper environment lineage;
- parent edge, insertion sequence, and depth;
- Jev sibling rank and bounded path preference;
- measured cumulative Lean, Jev, and LLM costs;
- generated families and attempted actions.

The theorem owns one budget ledger. Child nodes reference it; they do not receive copies of remaining resources.

### Scheduler

Use these deterministic rules for the first controller:

- select the first open goal reported under the frozen `autoResume` policy;
- pop the lowest-priority frontier node, breaking ties by insertion sequence;
- expand each `(lineage, replay-prefix hash)` once;
- increment depth for each accepted tactic or helper-patch edge;
- cap the frontier at 256 nodes and evict the worst score, recording the eviction;
- never reopen evicted nodes during ordinary search;
- retain one age-priority slot in every eight pops so model scores cannot starve old branches;
- reserve theorem budget for fallback and final replay before tactical expansion begins.

A node expansion discovers and executes actions in diverse batches of at most eight. The first batch allocates up to two local actions, two suggestions, one logical/structural tactic, one automation tactic, one arithmetic/decision tactic, and one retrieval action. Widen through four batches only when the node remains eligible and budget remains. Cap discovery at 32 concrete actions.

A candidate attempt includes discovery cost and execution. A successor is accepted when Lean changes the complete state or closes it. Record invalid, unchanged, timed-out, and duplicate-prefix outcomes.

After each batch, ask Jev to rank the verified sibling successors. Retain at most eight tactical successors across the expansion. Logged but discarded siblings return only in a separately budgeted audit, never silently during search.

Initial priority uses deterministic depth, measured cost, sibling rank, and age. No state is pruned solely by an uncalibrated Jev probability.

## Jev questions

Use three distinct question schemas.

### Sibling preference

Given one parent and its Lean-verified successors:

> Under continuation policy P and remaining budget B, which transition is the best next expansion toward closing every goal?

Treat Choice probabilities as sibling preference signals. `none_of_these` means that spending the next expansion on any displayed successor has lower expected utility than route escalation under the same budget.

### Route selection

Choose among:

- widen Lean action families;
- request direct generated actions;
- request a lemma patch;
- request a generalization patch;
- request a definition patch;
- abandon this node under the current budget.

Deterministic exhaustion can force fallback even when Jev selects widening.

### Path estimate

Define the event exactly:

> The configured continuation policy closes every goal in this complete state within 16 further node expansions using the displayed allowed families and remaining provider budget.

Ask this independently for retained states. Until held-out calibration exists, transform the answer into a bounded heuristic bonus rather than a probability cost. Refresh it only when the state is reconstructed under a different remaining-budget bucket.

Evaluate discrimination and calibration on a development split with fixed continuations and budgets. A budget-censored failure is not evidence that a state is unprovable. Never multiply separate-goal estimates.

## Direct generation

Trigger direct generation when Lean yields no retained tactical successor, all batches are exhausted, or the theorem reaches its configured tactical-expansion threshold.

The provider receives the complete state, attempted action families, bounded admissible premises, and remaining budget. It returns up to eight tactics or terms. Execute them from the ancestor state and rank verified successors like Lean-generated actions.

Use only GLM 5.3 Flash and DeepSeek V4.1 Flash in the first live fallback experiment. Record exact resolved provider model IDs.

## Helper contracts

The first implementation accepts only complete, hole-free helper declarations. A helper's internal proof obligations must be discharged before the declaration becomes available. The search may continue only on goals left by applying the completed helper to the unchanged target.

Use three generation contracts.

### Lemma patch

Return bounded private declarations over existing objects and a target action that uses them. Invariants, closure-lifting facts, bridges, normalization lemmas, and preservation facts use this contract.

Acceptance requires every declaration to elaborate, remain within the admissible dependency environment, and change the target proof state through the supplied action. An unused helper is not progress.

### Generalization patch

Return a complete strengthened theorem and a Lean-checked adapter from it to the original target with its original binders and assumptions. The strengthened theorem must differ under an explicit binder or premise generalization. Accumulator generalization and strengthened induction use this contract.

### Definition patch

Return a conservative private definition, complete bounded API lemmas, and a target action that uses the new API. Acceptance includes termination checking, declaration and dependency limits, and demonstrated use in the target transition.

Directly elaborating witness terms remain tactical actions. A named witness fact over existing objects is a lemma patch.

For each structural request:

1. request at most three complete patches under one selected contract;
2. enforce declaration count, source size, dependency depth, and compilation limits;
3. reconstruct the verified target prefix in an isolated child environment;
4. compile every declaration without holes;
5. run the target action and inspect its complete successor state;
6. reject target changes, new axioms, unsafe/native dependencies, leakage, and unused helpers;
7. show only accepted patch successors to Jev;
8. add retained child lineages to the ordinary frontier.

Helper lineages are source-level artifacts. Reconstruct them after worker loss by replaying declarations and the target prefix in a fresh isolated process.

## Initial resource policy

Treat these as frozen experiment defaults:

- 8 actions per incremental batch and 32 discovered actions per expansion;
- 8 retained tactical successors;
- 3 generated patches per structural request;
- 128 expanded nodes and frontier capacity 256;
- depth limit 24;
- 2 seconds per ordinary tactic and 10 seconds per broad automation action;
- 2 direct-generation rounds and 1 structural-generation round;
- 5 minutes wall time per theorem;
- 20% of remaining wall time and provider budget reserved for fallback;
- final replay budget reserved before search starts.

The supervisor measures wall and process-tree CPU because Pantograph does not provide complete per-action CPU accounting. Profile actual distributions before increasing batch or node limits. Compile-before-rank is retained only if downstream expansions saved by Jev offset its measured speculative execution cost.

## Trust and acceptance

A claimed proof passes all of these checks:

1. all generated text ran in the restricted worker sandbox;
2. the original statement, imports, options, and prior declarations are unchanged;
3. Lean elaborates the submitted declarations and proof from source in a fresh process;
4. root inspection finds no unresolved metavariables;
5. no prohibited holes, axioms, unsafe dependencies, or native-evaluation evidence occur transitively;
6. every helper lies in the private generated namespace and is used by the accepted proof lineage.

Store the clean replay output and dependency audit with the result.

## Cache and replay

Content-address persistent transitions by repository commit, toolchain, manifest, source-prefix hash, namespace/options state, helper-lineage source hash, replay prefix, action text, and resource class. Re-execute a cached transition when any environment component differs.

Content-address provider calls by exact model, prompt schema, complete state, options, resource bucket, and sampling parameters. Store raw response hashes without headers or credentials.

Network-free decision replay reconstructs requests and verifies recorded choices. Fresh Lean replay independently re-elaborates accepted source. Report these as separate guarantees.

## Failure evidence

Allow `unknown` and `budget_censored`. Assign a stronger cause only with evidence:

- `backend_failure`: initialization, execution, recovery, or replay failed;
- `no_generated_candidate`: no action was materialized;
- `candidate_oracle_failure`: a separately budgeted audit found no successful continuation in the frozen candidate graph;
- `retrieval_miss`: an audit identifies an admissible useful declaration absent from retrieved candidates;
- `verification_failure`: generated text did not produce an accepted transition;
- `retention_failure`: an audited successful successor was discarded after verification;
- `ranking_or_scheduling_failure`: an audited successful branch was retained but remained below the execution cutoff;
- `direct_generation_failure`: all direct candidates were invalid or non-progressing;
- `helper_generation_failure`: every patch failed its contract;
- `helper_continuation_failure`: an accepted patch left obligations unsolved;
- `budget_censored`: a configured resource ceiling ended search;
- `unknown`: available evidence does not distinguish causes.

Inject each failure class in scheduler and integration tests. The candidate oracle is an upper bound over an audited finite graph, not over all Lean proofs.

## Delivery plan

### 1. Pantograph feasibility spike

Implement the ten gate tests and latency profile. Run every Lake setup and build through `lean-cache`. Decide whether Pantograph remains the backend.

### 2. Scheduler model

Implement the theorem ledger and a pure deterministic scheduler simulator. Test goal selection, incremental batches, frontier capacity and eviction, age exploration, tie-breaking, fallback reserves, worker-loss invalidation, and every failure attribution.

### 3. Catalogue-only prover

Connect current-version theorem holes to dynamic Lean suggestions, local applications, automation, retrieval, source replay, and dependency audit. Establish candidate coverage and an automation-portfolio baseline before adding Jev.

### 4. Jev ranking

Add sibling preference, route selection, and bounded path estimates. Compare Jev best-first, deterministic best-first, diverse beam, random ordering, and audited candidate oracle under identical frozen candidates and theorem-level resource ceilings.

### 5. Direct and helper generation

Add the two allowed direct-action providers. Then add complete lemma, generalization, and definition patches. Include helper-requiring integration tasks before any external benchmark gate.

### 6. Benchmark compatibility

Run each external benchmark in its original environment with a compatible backend, or publish a frozen audited port with every changed or excluded task. LeanDojo Benchmark 4 uses Lean `v4.10.0-rc1`; miniCodeProps uses Lean `v4.9.0`. Neither is silently upgraded to the Pantograph 4.30 environment.

Use LeanDojo `novel_premises` for Mathlib search, miniCodeProps medium/hard for induction and helpers, LeanCat for retrieval, and TheoremBench for helper structure. Freeze tasks, prompts, indexes, budgets, seeds, and source extraction before live calls. A foundation model may still have seen public test data; report that limitation.

## First end-to-end gate

Before external benchmarks, run frozen Lean 4.30 project tasks containing tactical, retrieval, generalization, lemma, and definition cases. Then run two compatible LeanDojo sets:

- 100 random test theorems for overall solve rate;
- 100 theorems with at least three recorded steps that survive the frozen automation portfolio.

For ranking-only comparisons, use identical frozen candidate graphs. For end-to-end comparisons, apply common wall, Lean CPU, and provider-token ceilings and report how policies generated different candidates.

Proceed to miniCodeProps when the controller:

- replays and dependency-audits every claimed proof;
- reports all speculative work, recovery, discarded patches, and replay cost;
- shows candidate-oracle headroom over deterministic scheduling;
- improves solve rate or cost over direct generation on the automation-resistant set;
- demonstrates all three complete helper contracts;
- completes network-free decision replay and fresh Lean replay.

## First CLI

```bash
python3 -m jevlean.prove \
  --repo /path/to/lean/project \
  --file Path/To/File.lean \
  --theorem Namespace.target \
  --config configs/mvp.toml
```

The command writes submitted Lean source, helper declarations, dependency audit, clean replay result, and a machine-readable run trace. Benchmark runners call the same controller API.

## References

- Search architecture report: https://bot.jesyspa.dev/lean/jev/search-architecture-20260917/
- Lean interface report: https://bot.jesyspa.dev/lean/jev/lean-interface-20260917/
- LeanDojo: https://github.com/lean-dojo/LeanDojo
- Pantograph: https://github.com/stanford-centaur/Pantograph/tree/7076ab3632b5de67a4f83ab259b23b37acaea1d0
- miniCodeProps: https://github.com/cmu-l3/minicodeprops-eval
- LeanCat: https://arxiv.org/abs/2512.24796
- TheoremBench: https://arxiv.org/abs/2606.09450
