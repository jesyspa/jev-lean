# Jev-first proof-search controller

Use this architecture for a controller that tries a broad code-generated Lean action catalog before paying for generative proof search.

## Search boundary

Code owns action enumeration, Lean execution, queues, budgets, and traces. Jev supplies bounded semantic choices. Lean remains the only correctness oracle. An LLM supplies text only after Jev selects a fallback route or verified catalog search is exhausted.

Represent each search node by:

- the normalized local context and goals;
- a replayable sequence of verified actions;
- deterministic features such as goal count, depth, and duplicate-state hash;
- the Jev distribution and resolved model ID;
- remaining Lean, Jev, and LLM budgets.

## Expansion loop

1. Enumerate parameterized actions in code. Include premise application, constructors, rewriting, simplification, extensionality, induction/cases over local variables, arithmetic solvers, decision procedures, and bounded automation.
2. Ask one Jev `Choice` to rank the concrete actions. Include `llm_tactic_fallback` and `llm_helper_fallback` as explicit options with distinct criteria.
3. Execute catalog actions in descending Jev probability. Start with the top action, but retain the full distribution. Reject elaboration failures, unchanged states, timeouts, and duplicate successors.
4. Return a completed proof immediately. Otherwise place verified successors in a bounded best-first queue. Combine Jev probability with deterministic depth and duplicate penalties in code.
5. Invoke a direct-proof LLM only when Jev selects `llm_tactic_fallback` or every catalog action above the calibrated execution threshold fails.
6. Invoke a structural-patch LLM only when Jev selects `llm_helper_fallback`. Request private helper declarations and a revised proof. Compile the patch in a generated module and recheck it from a clean replay.

Do not treat Jev confidence as proof validity. The prototype contains a wrong tactic selection at confidence 0.59 and correct selections at lower confidence. Calibrate execution and fallback thresholds on held-out states.

## Premise selection

Retrieve candidates deterministically before asking Jev:

1. collect local hypotheses and declaration names referenced by the goal;
2. query bounded name/type search over the imported environment;
3. deduplicate and cap the list;
4. ask Jev to choose among complete candidate signatures plus `none`;
5. execute an `exact`, `apply`, `rw`, or `simpa using` family around the selected declaration and let Lean decide.

The committed lemma task is only a sanity check because the correct signatures nearly repeat the goals. A realistic benchmark needs larger, confusable candidate sets produced by the actual retriever.

## Fallback contract

Keep the LLM behind a provider-neutral interface:

```text
proposeDirect(state, attemptedActions, diagnostics) -> candidate actions
proposePatch(declaration, obstruction, boundedPremises) -> declarations + revised proof
```

Require structured output containing candidate IDs, action kind, Lean source, provider, exact model ID, sampling parameters, usage, and response hash. Never execute generated source outside the Lean sandbox. Never send repository configuration, credentials, comments, or unrelated declarations.

## Trace and replay

Content-address each model request from the pinned model, question schema, normalized state, and candidates. Record the exact raw response, its hash, resolved model, usage, and latency. Keep headers and credentials out of traces.

Replay must:

- reconstruct every public request and verify its hash;
- validate response types and probability keys;
- rerun accepted actions in Lean;
- report model calls without making network requests.

`jevlean.experiment.verify_trace` and `check_lean` implement this boundary for the prototype.

## Next evaluation

Run a held-out study of at least 100 representative theorem holes. Generate action templates from local syntax rather than using fixed variable names. Compare at matched Lean CPU and model budgets:

- fixed catalog order;
- seeded random order;
- deterministic automation-first order;
- Jev-ranked catalog execution;
- direct LLM generation;
- Jev-first execution with direct and structural LLM fallbacks.

Report verified solve rate, time to first proof, actions executed, fallback precision/recall, helper-patch success, premise recall and ranking, replay success, tokens, latency, and cost. Use repeated runs if model output is not deterministic. The current 12-goal result supports a larger trial, not production deployment.
