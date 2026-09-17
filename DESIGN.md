# Jev-first Lean prover implementation proposal

Use this proposal to build the first end-to-end prover. The system searches verified Lean states, uses Jev to choose promising paths, and calls a generative LLM when ordinary proof actions or the current proof formulation are insufficient.

## Success criterion

The first system accepts a theorem declaration with its proof removed and returns either:

- a proof and optional private helper declarations that compile in a clean replay; or
- a structured failure record identifying the exhausted subsystem and remaining frontier.

Measure the system by verified solve rate against total Lean CPU, wall time, Jev calls, and generative-model tokens. Report results separately for all tasks and for tasks that a bounded Aesop prepass does not solve.

## System boundary

The controller is an external Python process. It owns search, budgets, caching, provider calls, and traces. A supervised Pantograph subprocess owns Lean elaboration and proof states. Lean is the correctness oracle. Jev ranks bounded alternatives. A generative LLM writes candidate Lean text.

Pin Pantograph to commit `7076ab3632b5de67a4f83ab259b23b37acaea1d0` with Lean 4.30 for the first implementation. Hide Pantograph behind a small backend interface so another pinned benchmark environment or a project-owned worker can replace it.

```text
CLI / benchmark runner
        |
        v
Python search controller
  |        |             |
  |        |             +-- LLM providers: direct actions and helper patches
  |        +---------------- Jev: sibling choice and path-success estimates
  +------------------------- supervised Pantograph worker
                                  |
                                  +-- Lean/Mathlib automation and suggestions
                                  +-- tactic execution and successor states
                                  +-- temporary helper environments
                                  +-- clean replay
```

Never accept model text without Lean verification. Never expose credentials, unrelated source, comments, or repository configuration to model providers.

## Lean backend interface

Implement this interface before the search policy:

```python
initialize(task) -> ProofSession
suggest(session, family, limit) -> list[Action]
execute(session, action, timeout) -> Invalid | Solved | Successor
install_patch(session, patch, timeout) -> Invalid | PatchSuccessor
normalize(session) -> NormalizedState
replay(task, proof, declarations, timeout) -> ReplayResult
close(session) -> None
```

`NormalizedState` contains all open goals, local declarations, target expressions, environment lineage, and a stable hash. The hash includes the imported environment and installed private declarations. Pretty-printed binder names are normalized before hashing where Lean exposes stable expression data.

`Successor` contains the action, all resulting goals, diagnostics, elapsed Lean CPU, and normalized hash. `PatchSuccessor` also contains the new environment lineage and every obligation introduced by the patch.

Run one persistent worker per theorem. The supervisor enforces a hard process limit outside Lean. On timeout or crash, kill the worker, start a fresh worker, and replay the verified prefix. A completed result is accepted only after replay in a fresh process.

## Reuse Lean automation

Treat Lean and Mathlib as the action generator. The controller orchestrates existing tools instead of implementing tactic semantics.

Always make these bounded action families available when their syntax applies:

- local closure and application: `assumption`, `exact`, `apply`, `refine`, and `solve_by_elim` over local declarations;
- rewriting and simplification: both directions of local equalities, `rw?`, `simp?`, `simp`, and `simpa`;
- environment search: `exact?`, `apply?`, `library_search`, and bounded declaration lookup by type and name;
- logical structure: `intro`, `constructor`, `left`, `right`, `use`, `ext`, `funext`, `cases`, and `induction`;
- automation: bounded `aesop`, `grind`, `tauto`, and `contradiction`;
- decision and arithmetic procedures: `decide`, `native_decide`, `omega`, `norm_num`, `linarith`, `nlinarith`, and `ring`.

Materialize suggestions into concrete replayable actions. Include declaration names and signatures in retrieval actions. Apply per-action heartbeat and wall-time limits so broad automation cannot consume the theorem budget.

The first backend acceptance test demonstrates:

1. dynamic `exact` and `apply` actions from local hypotheses;
2. one retrieved Mathlib declaration;
3. branching from one state into two independently executable successors;
4. preservation of multiple goals;
5. timeout recovery by replay;
6. installation of a private helper lemma in a child environment;
7. clean proof replay.

## Search representation

Use bounded AND/OR best-first search.

An OR node is a complete Lean proof state. Its outgoing edges are alternative tactical actions or environment patches. An AND edge is complete only when every goal or helper obligation introduced by that edge is solved. The implementation may keep Lean's multi-goal state intact, but the search record preserves the AND relationship for scoring and failure attribution.

Each node stores:

- normalized Lean state and environment lineage;
- replayable verified prefix;
- parent edge and depth;
- remaining Lean, Jev, LLM, and wall-time budgets;
- deterministic features such as goal count and expression size;
- Jev path-success estimate and sibling distribution;
- generation source and accumulated cost.

Deduplicate nodes by normalized state plus environment lineage. Retain the cheaper prefix when two paths reach the same node.

## Tactical expansion

Expand a tactical OR node as follows:

1. Ask Lean for local and suggestion-derived concrete actions.
2. Add applicable bounded automation actions.
3. Execute candidates in Lean with short limits.
4. Drop invalid, unchanged, timed-out, and duplicate successors.
5. Return immediately when a candidate closes every goal.
6. Present the verified successors to Jev.
7. Insert the retained successors into the global frontier.

Jev sees the current state and, for each option, the concrete action and resulting state. The question is:

> Which verified transition is most likely to lie on a short, robust path to a complete proof?

Use one `Choice` to rank siblings. Include a `none_of_these` option. Use independent path-success questions to compare nodes from different sibling sets. Do not compare raw Choice probabilities produced from different option sets.

The initial frontier priority is:

```text
path failure cost
+ depth penalty
+ measured Lean/model cost penalty
+ duplicate and repeated-family penalty
- diversity bonus
```

Convert the Jev path-success response to a clipped negative log cost. Keep every coefficient in run configuration and report it. Begin with equal-cost tie-breaking and calibrate coefficients only on a development split.

## Structural expansion

A structural branch changes the local proof environment. It is appropriate when progress likely requires a stronger statement, reusable fact, witness construction, or new representation.

Use three execution contracts:

### Lemma patch

The LLM returns one or more private lemmas and a revised next action. Invariants, closure-lifting lemmas, bridge lemmas, normalization facts, and preservation facts use this contract.

### Generalization patch

The LLM returns a strengthened theorem, its proof obligations, and a derivation of the original goal. Accumulator generalization and strengthened induction hypotheses use this contract.

### Definition patch

The LLM returns a private definition, the minimum API lemmas required to use it, and a revised proof path. New compositional objects and representations use this contract.

Witnesses that elaborate directly remain tactical actions. A witness becomes a lemma patch when its construction creates independent obligations worth searching separately.

For a structural expansion:

1. Give the LLM the target, bounded relevant declarations, attempted action families, and obstruction summary.
2. Request up to three patches under one explicit contract.
3. Parse each response as structured data containing declarations, target action, model metadata, and generation parameters.
4. Compile each patch in a child environment.
5. Reject patches that weaken or alter the target, add axioms, contain holes, or fail elaboration.
6. Present only verified patch successors and their remaining obligations to Jev.
7. Add selected child lineages to the same frontier as tactical successors.

Jev may request a structural family before tactical exhaustion. Deterministic stagnation also triggers structural generation so a mistaken Jev route cannot suppress fallback.

## Stagnation and fallback

Trigger a direct-action LLM request when any of these holds:

- Lean produces no new tactical successor;
- all tactical successors within the per-node execution budget have been exhausted;
- the frontier repeats the same action family without reducing the best path-failure cost;
- the theorem reaches its configured tactical expansion threshold without a proof.

The direct-action provider returns up to eight tactics or terms. Execute and rank valid successors exactly like code-generated actions.

Trigger structural generation when:

- Jev selects a structural family with sufficient calibrated probability;
- induction repeatedly fails because the induction hypothesis is too specific;
- relation or preservation goals recur under a changed accumulator or constructor;
- direct fallback has produced no verified improving successor in two rounds;
- the tactical frontier is exhausted.

Defaults are experimental configuration rather than architecture rules.

## Initial bounded policy

Use these defaults for the first runnable controller:

- 32 concrete actions per tactical expansion;
- 8 retained tactical successors;
- 3 generated patches per structural expansion;
- 128 expanded nodes per theorem;
- depth limit 24;
- 2 seconds per ordinary tactic and 10 seconds per broad automation action;
- 2 direct-generation rounds and 1 structural-generation round;
- 5 minutes wall time per theorem.

Run a deterministic automation prepass with a separate small budget. Record its solves as part of overall performance and exclude them from the conditional Jev-search metric.

## Provider contracts

Keep generation provider-neutral:

```text
propose_direct(state, attempts, premises, count) -> DirectCandidate[]
propose_lemma_patches(task, obstruction, premises, count) -> LemmaPatch[]
propose_generalizations(task, obstruction, premises, count) -> GeneralizationPatch[]
propose_definition_patches(task, obstruction, premises, count) -> DefinitionPatch[]
```

Each candidate records provider, exact model ID, sampling parameters, token use, response hash, and source text. The first live fallback evaluation allows only GLM 5.3 Flash and DeepSeek V4.1 Flash.

## Trace, cache, and failure attribution

Content-address Lean transitions by environment lineage, normalized state, action text, Lean version, and timeout class. Content-address provider calls by model, prompt schema, state, candidates, and sampling parameters.

A run log records every generated option, Lean result, Jev distribution, frontier operation, budget charge, and replay result. Offline replay validates hashes and performs no network calls.

Assign every failure one primary cause and retain supporting causes:

- backend initialization, timeout, or replay failure;
- no generated candidate;
- candidate oracle failure under the search budget;
- premise retrieval miss;
- Jev ranking placed a successful branch below the frontier cutoff;
- direct LLM produced no valid improving action;
- structural generation produced no valid patch;
- verified patch created unsolved obligations;
- node, depth, cost, or wall-time budget exhaustion.

This taxonomy determines the next engineering change.

## Delivery sequence

### 1. Backend spike

Implement the Pantograph adapter and its seven acceptance tests. Use `lean-cache` for every Lake setup and build. Stop if the pinned backend cannot install helper declarations or replay branches reliably; evaluate a project-owned worker at that point.

### 2. Catalogue-only prover

Implement dynamic Lean action enumeration, transition caching, normalized deduplication, and bounded best-first search. Run without Jev to establish candidate-oracle coverage and deterministic baselines.

### 3. Jev path ranking

Add sibling Choice and cross-node path-success questions. Compare Jev best-first, deterministic best-first, diverse beam, random ordering, and candidate oracle under matched Lean CPU and node budgets.

### 4. Direct generation

Add both allowed fallback models behind the direct-action contract. Compare catalogue-only, always-LLM, and Jev-first escalation at matched total cost.

### 5. Structural generation

Add the three patch contracts and child environment lineages. Compile multiple patches before Jev ranks them. Measure patch validity, remaining-obligation solve rate, and final theorem solve rate.

### 6. External evaluation

Use LeanDojo Benchmark 4 `novel_premises` for transition and Mathlib proof search, then miniCodeProps medium/hard for induction and helpers. Add LeanCat for library retrieval and TheoremBench for explicit versus generated helper structure.

Freeze task selection, prompts, candidate budgets, and scoring configuration before live evaluation.

## First end-to-end gate

Run two frozen LeanDojo sets:

- 100 randomly sampled test theorems for overall solve rate;
- 100 test theorems with at least three recorded steps that survive the automation prepass.

For each set report deterministic automation, catalogue-only best-first, Jev-ranked search, direct LLM, and full Jev-first search. Match Lean CPU, node limits, and provider-token budgets where the policies permit it.

Proceed to miniCodeProps when the controller:

- replays every claimed proof cleanly;
- provides a primary diagnosis for every failure;
- demonstrates candidate-oracle headroom over deterministic search;
- demonstrates either higher solve rate or lower cost than direct generation;
- completes network-free trace replay.

## First CLI

The first user-facing command is:

```bash
python3 -m jevlean.prove \
  --repo /path/to/lean/project \
  --file Path/To/File.lean \
  --theorem Namespace.target \
  --config configs/mvp.toml
```

It writes a proof artifact, optional private declarations, a clean-replay result, and a machine-readable run trace. Benchmark runners call the same controller API.

## References

- Search architecture report: https://bot.jesyspa.dev/lean/jev/search-architecture-20260917/
- Lean interface report: https://bot.jesyspa.dev/lean/jev/lean-interface-20260917/
- LeanDojo: https://github.com/lean-dojo/LeanDojo
- Pantograph: https://github.com/stanford-centaur/Pantograph
- miniCodeProps: https://github.com/cmu-l3/minicodeprops-eval
- LeanCat: https://arxiv.org/abs/2512.24796
- TheoremBench: https://arxiv.org/abs/2606.09450
