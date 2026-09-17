# Jev-guided Lean proof search

## Recommendation

Build an external proof-search controller first. The controller should ask an LLM for candidate Lean actions, check every action in Lean, and use Jev only to rank the checked alternatives. Keep the Lean kernel as the sole authority on correctness.

Use Python for orchestration and a persistent Lean subprocess for proof states. Expose a command-line interface before adding editor or tactic integration. A tactic can later be a thin client that sends the current goal to the controller and inserts the returned proof.

This division matches the models:

- the LLM generates tactics, proof fragments, helper lemmas, and definitions;
- Lean rejects invalid actions and reports exact successor goals;
- Jev makes bounded choices among named candidates and returns a probability distribution;
- ordinary code owns search, budgets, caching, retries, and source edits.

Jev is not a text generator or a reliable calculator. Its documented strengths are narrow typed judgments over closed answer sets. Its documented weaknesses include multi-hop reasoning, irrelevant context, arithmetic, and generation. Proof search should therefore test Jev as a ranker rather than assume it understands Lean well enough to act as a prover.

## Proposed search loop

Represent a search node by the initial declaration, a replayable sequence of accepted actions, the current goals, and budget metadata.

For each expanded node:

1. Normalize the current goals and local context. Remove source comments and unrelated file text.
2. Ask the LLM for a JSON array of candidate actions. Each action contains Lean source and a declared action kind. Treat any rationale as untrusted metadata.
3. Execute candidates from the same parent state in Lean. Discard timeouts, elaboration failures, unchanged states, and duplicate successor states. Return a completed proof immediately.
4. Give Jev the parent goals and the surviving candidates with their successor goals. Use stable opaque candidate IDs.
5. Ask narrow questions in one request. The first baseline should combine:
   - a `Choice` selecting the candidate most likely to contribute to a complete proof, with `none` as an option;
   - one `Noul` per candidate asking whether the successor is meaningful progress rather than a detour.
6. Put candidates into a best-first or bounded-beam queue. Code combines Jev probabilities with deterministic penalties for depth, time, repeated states, and excessive branching.
7. When the tactical search stagnates, ask the LLM for a structural patch containing a helper lemma or definition. Compile it in a sandbox module, restart the affected search branch, and retain it only if Lean verifies both the declaration and its use.

The full probability map must be logged. A winning label alone loses the information needed for confidence gates and evaluation.

## Decisions to make

### 1. Integration boundary

**Recommended:** an external controller with a later thin tactic/editor adapter.

A native Lean tactic gives good access to `MetaM`, but network clients, concurrent model calls, durable traces, provider configuration, and process recovery are awkward inside elaboration. An external controller is also easier to benchmark headlessly. The cost is that a machine interface to Lean must be selected and maintained.

### 2. Lean machine interface

Candidates are:

- a small project-owned Lean JSON worker built on Lean elaborator APIs;
- Pantograph's JSON REPL;
- Lean's LSP/RPC protocol;
- generated scratch files compiled by `lake env lean`.

**Recommended:** begin with a small project-owned persistent worker if its prototype stays small. Use scratch-file compilation as the correctness oracle for structural patches. Evaluate Pantograph before adopting it as a dependency. LSP/RPC is suitable for an editor adapter, but it adds document-version and widget-RPC complexity to the search engine. Starting a compiler for every tactic is too slow for the final loop.

This decision needs a one-day spike against the chosen Lean version. The spike must demonstrate independent branching from one parent state, multiple goals, timeout recovery, pretty-printed local contexts, and replay.

### 3. Search unit

Candidates can be single tactics, tactic blocks, terms, or arbitrary source patches.

**Recommended:** use a tagged union:

- `tactic`: one tactic or a short tactic sequence applied to the current goals;
- `term`: a complete term for the focused goal;
- `structuralPatch`: declarations plus a revised proof, evaluated only at a stall boundary.

Single-tactic transitions make branching and replay manageable. Structural patches need a slower compile-and-restart path because they can change elaboration context and invalidate live proof states.

### 4. Jev question design

Possible designs are one large `Choice`, independent `Noul` questions, pairwise comparisons, or several rubric scores.

**Recommended:** benchmark a `Choice + Noul` design first. A Choice provides relative ranking, while candidate-specific Nouls can reject a set in which every candidate is poor. Include `none` in every relative choice. Batch all questions sharing a state into one API request.

Do not ask Jev to count goals, compare numeric costs, prove type correctness, or calculate a combined score. Lean and controller code can do those exactly. Keep instructions literal and give every option an explicit description.

### 5. Candidate representation sent to Jev

The options are raw Lean state, an LLM-written synopsis, deterministic features, or combinations of them.

**Recommended:** establish raw normalized parent/successor states as the baseline. Test a short LLM strategy synopsis as an ablation, because it may make Lean syntax easier to judge but can also launder an incorrect rationale into a persuasive one. Keep deterministic facts such as goal count and execution time out of the semantic judgment and combine them in code.

### 6. LLM provider and contract

**Recommended:** define a provider-neutral interface and support one OpenAI-compatible endpoint in the prototype. Require structured output with candidate IDs, action kinds, Lean source, and optional strategy synopsis. Record provider, exact model ID, sampling parameters, prompt hash, token usage, and raw response.

The specific LLM remains an open product decision. It should be chosen through a small candidate-generation benchmark rather than by chat quality. The benchmark should measure the fraction of generated actions that elaborate and the fraction that close or improve a held-out goal.

### 7. Search policy

**Recommended:** bounded best-first search with a beam cap and deterministic budgets. Breadth-first search wastes LLM calls; greedy search makes Jev's first mistake terminal. Queue priority should use a configurable combination of Jev probability, path depth, duplicate detection, and deterministic progress features.

All limits belong in configuration: wall time, Lean execution time per action, LLM calls, Jev calls, nodes, depth, and total input tokens.

### 8. Auxiliary declarations

**Recommended:** let the LLM propose private helper lemmas and local definitions only after tactical search stalls. Test patches in a generated sandbox module and never mutate the user's file during search. On success, emit a source patch for review or insertion. Require the final declaration to compile from a clean replay and reject unused helpers.

A later version can recursively prove helper lemmas by placing each one into the same search queue. The first version should ask the LLM for helper proofs as part of one structural patch to avoid an unbounded theorem-dependency graph.

### 9. Premise retrieval

**Recommended:** use deterministic local-environment search before adding a learned retriever. Start with names from the local context, declaration-name/text search, and Mathlib search facilities. Give the LLM a bounded premise list. Jev can rerank retrieved premises in a separate experiment, but premise ranking and tactic ranking should not be conflated initially.

Lean Copilot demonstrates native tactic generation and proof search, but its bundled premise retriever is tied to a fixed historical Mathlib snapshot. Its model interfaces and Aesop integration are useful prior art, not necessarily a dependency.

### 10. Model version, confidence, and fallback

**Recommended:** pin `jev-1.13.0` during experiments and log the resolved model returned by the API. Do not tune against the moving `jev-latest` alias. Treat confidence thresholds as empirical parameters. If Jev is uncertain or selects `none`, expand the next candidate by a deterministic fallback policy or ask the LLM for a more diverse batch.

No universal confidence threshold should be built into the framework. Calibrate it on proof-search data.

### 11. Caching and reproducibility

**Recommended:** content-address every model call and Lean transition. Hash the model ID, prompt or question schema, normalized state, candidates, and relevant configuration. Store append-only JSONL traces plus compact final summaries. Redact credentials and make source retention explicit.

Replay mode must run without network calls. It should recheck successful proofs in Lean and reconstruct search decisions from cached responses.

### 12. Data and privacy

**Recommended:** make external transmission opt-in and show which declaration/context will be sent. Send the smallest useful state. Never send API keys, repository configuration, unrelated source, or comments. Source text can contain prompt injection; treat it as data and delimit it structurally.

### 13. Dependency policy

The implementation will likely want TypeSafe's official Python SDK and an LLM client. These are non-Mathlib dependencies and should be approved before addition. A standard-library HTTP implementation avoids a runtime SDK dependency but increases transport, validation, and retry work.

**Recommended:** approve the official `typesafe-sdk` after a small API spike, and keep the LLM provider behind a narrow interface. Avoid adding Lean Copilot or Pantograph until their compatibility and value have been measured.

## Evaluation plan

The central question is not whether Jev can sometimes choose a good-looking tactic. It is whether Jev improves verified proof search at a matched budget.

Build a version-pinned corpus of theorem holes with a mix of:

- direct premise and simplification proofs;
- induction and case analysis;
- arithmetic;
- rewriting and extensionality;
- multi-goal proofs;
- problems where a helper lemma is useful.

Exclude exact duplicates from prompts and keep a sealed test split. Compare:

1. deterministic Lean automation such as `aesop`, `simp`, and `omega` under a time budget;
2. LLM generation with deterministic heuristic ranking;
3. LLM generation with random ranking;
4. LLM generation with Jev ranking;
5. Jev ranking with and without successor goals or LLM synopses.

Report verified solve rate, time-to-first-proof, expanded nodes, candidate elaboration rate, Lean CPU time, LLM and Jev input tokens, estimated cost, proof length, replay success, and failure category. Match conditions by LLM candidate batches and search budgets. Use several fixed sampling seeds and publish raw traces with source-sensitive fields removed.

Before building the full controller, run an offline ranking study. Generate and Lean-check candidate transitions for roughly 100 goals, label each candidate by whether a bounded continuation search eventually succeeds, and test whether Jev's ranking beats deterministic and random baselines. This directly tests the risky assumption at low implementation cost.

## Delivery plan

### Phase 0: project and API spikes

1. Create a Lake project pinned to one Lean/Mathlib revision.
2. Run `lean-cache use .` immediately after creating the Lake files. Verify with `lean-cache check-env`, and use `lean-cache build --wait .` for full builds. Keep the `.lake/packages` per-package shared-cache links installed by `lean-cache`.
3. Build the smallest persistent Lean worker that branches and replays tactic states.
4. With an explicit `TYPESAFE_API_KEY`, call `GET /v1/models` and one batched `POST /v1/systemone` request. Record the actual response schema, latency, usage, and resolved model.
5. Confirm the chosen LLM can return validated structured candidate batches.

Exit criterion: one theorem can be searched through several independently checked branches, with all calls replayable from disk.

### Phase 1: ranking experiment

Create the transition dataset and compare Jev question formats. Decide whether Jev contributes enough signal to continue. This is a deliberate stop/go gate.

Exit criterion: a preregistered ranking metric and matched-budget search simulation show an improvement worth testing end to end.

### Phase 2: tactical proof search

Implement bounded best-first search, batched candidate checking, model caches, budgets, traces, and baseline runners. Emit a proof without editing source. Recheck every emitted proof in a fresh Lean process.

Exit criterion: clean builds and a reproducible benchmark report comparing all tactical baselines.

### Phase 3: structural patches

Add sandboxed helper lemma and definition generation. Add patch minimization and clean-file verification. Measure it separately from tactical search.

Exit criterion: successful patches compile from a clean checkout and contain no `sorry` or unused generated declarations.

### Phase 4: user integration

Add a thin Lean tactic, editor command, or both. The command should submit the current declaration, stream search status, and offer the verified proof or patch. Keep credentials and network work in the external controller.

## Current constraints and unknowns

- The repository currently contains only a README and is not yet a Lake project, so `lean-cache` cannot be attached until project initialization.
- No `TYPESAFE_API_KEY` was available during this investigation. The TypeSafe API behavior and Jev's ability to rank Lean states were not tested live.
- Jev 1.13 documents a 64k-token combined request limit and a 32k limit for the state plus longest question. Choice supports up to 255 options. These are generous enough for batched candidate ranking, but concise state is still important.
- Published TypeSafe pricing for Jev 1.13 is $0.042 per million input tokens, with output tokens free. Pricing and rate limits can change and must remain configuration/log data rather than assumptions in the search policy.
- Compatibility of Pantograph and Lean Copilot with the project's eventual Lean/Mathlib pin has not been established.

## References

- TypeSafe introduction and primitives: https://docs.typesafe.ai/introduction and https://docs.typesafe.ai/primitives
- TypeSafe API and models: https://docs.typesafe.ai/api and https://docs.typesafe.ai/models
- Jev 1.13 known limitations: https://docs.typesafe.ai/model-jaggedness/jev-1.13
- TypeSafe workflow guidance: https://docs.typesafe.ai/concepts/how-to-build-with-system-one
- Lean Copilot: https://github.com/lean-dojo/LeanCopilot
- Pantograph: https://github.com/stanford-centaur/Pantograph
