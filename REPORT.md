# Jev ranking for verified Lean proof progress

## Result

On 15 held-out synthetic Lean goals, Jev ordering found a verified useful next action within three attempts on 14 goals (93.3%). Aesop-first ordering solved 13 (86.7%), the deterministic ordering solved 11 (73.3%), seeded random ordering was expected to solve 8.234 (54.9%), and an oracle over the generated candidates solved all 15.

This is evidence that Jev can rank structural next steps, not evidence of end-to-end proof-search performance. The bounded continuations were written by the benchmark author and make many intended structural transitions recognizable.

## Operational protocol

The experiment changes the question from “which tactic closes?” to “which generated action has a verified path to a proof?” For each action, Lean checks both:

1. whether the action closes immediately; and
2. whether the action is accepted and one of at most three frozen continuation tactics closes every successor goal.

An action is useful exactly when either check compiles. No success label comes from author judgment. The committed matrix contains 18 initial states, 150 generated candidates, immediate-close results, and bounded-continuation results. Twelve held-out Jev top choices were useful multi-step transitions rather than immediate proofs. Nine of 15 held-out cases had no immediate proof from any of `aesop`, `simp`, `omega`, `ring`, or `rfl`; Aesop alone closed 5 of 15.

Concrete candidates come from a fixed standard set, `exact` and `apply` instantiations of named local hypotheses, induction/cases actions for local inductive variables, structural templates such as `funext x`, and top-six token-overlap retrieval from a frozen 32-declaration Mathlib index. Lean verifies invalid as well as valid candidates.

## Held-out ordering results

All policies receive the same candidates, continuations, and three-action execution budget. “Aesop-first” tries Aesop and then the deterministic order. The random result is the mean over 2,000 seeded permutations per case. The oracle puts any verified useful candidate first.

| Ordering | Solved within 3 | Rate |
| --- | ---: | ---: |
| Jev probability order | 14/15 | 93.3% |
| Aesop-first | 13/15 | 86.7% |
| Deterministic local/retrieval/structural order | 11/15 | 73.3% |
| Seeded random expectation | 8.234/15 | 54.9% |
| Verified-candidate oracle | 15/15 | 100% |

Jev ranked a useful action first on 12 of 15 goals. It missed the budget on the rewrite-and-ring case: it preferred `ring`, while `aesop` followed by the frozen `ring` continuation was the only useful generated path. On the left-inverse case, it ranked an invalid local `apply h` first and the useful retrieved injectivity theorem second. On the Finset cardinality case, the useful action appeared third.

## Separate lemma retrieval test

Ten held-out goals use deterministic top-six retrieval from the frozen compact index. Each candidate receives the same explicit local arguments and is checked by `exact`; this prevents a name label from serving as the success criterion.

| Policy | Verified useful lemma at top 1 |
| --- | ---: |
| Jev | 10/10 |
| Retrieval score alone | 7/10 |

The retriever included at least one verified lemma for all 10 goals. This is a controlled ranking test, not realistic Mathlib-scale retrieval: the index is small and manually assembled, and the signatures remain relatively distinctive.

## Separate structural-helper routing test

Six held-out states compare `direct_action` with `structural_helper`. The operational route is direct if any frozen generated action plus continuation compiles; otherwise it is helper, provided the supplied helper patch compiles. All six helper patches are checked by Lean.

| Route | Jev correct |
| --- | ---: |
| Direct action | 3/3 |
| Structural helper | 2/3 |
| Overall | 5/6 |

Jev incorrectly routed the accumulator-generalization case to direct action. No direct candidate passed the bounded check; the generalized induction helper compiled.

## Freeze, model, and cost

The benchmark, generator, retrieval index, questions, and all reconstructed request hashes were committed in `d3ca884` before the live calls. `data/progress-prompt-freeze.json` rejects any changed request. The 3 calibration progress cases are excluded from the 15-case headline result; all 10 lemma and 6 routing cases are held out. No prompt or candidate changed after the calls.

- Lean: `leanprover/lean4:v4.30.0`
- Mathlib: `v4.30.0`, commit `c5ea00351c28e24afc9f0f84379aa41082b1188f`
- Requested and resolved model: `jev-1.13.0` for all 34 requests
- Tokens: 25,123 input; 2,713 output
- Sequential API latency: 18.519925 seconds total; 0.544704 seconds/request mean
- Estimated input cost at the documented $0.042 per million input tokens: $0.001055166
- Actual billed cost: unavailable because the API responses provide usage but no currency amount

No fallback generative LLM was called. The trace stores public payload hashes, exact response bodies and hashes, usage, model IDs, and latency. It contains no request headers or credentials.

## Limitations

- The study is one deterministic-looking live pass over 15 synthetic held-out progress cases.
- The benchmark author chose the bounded continuations. Freezing prevents post-result tuning, but does not remove design bias.
- A compiled action-plus-continuation verifies a successor path but the artifact does not normalize or compare the intermediate pretty-printed goal state.
- Candidate generation uses metadata about local names instead of reading live Lean goals through an editor protocol.
- The compact retrieval index does not measure Mathlib-scale recall, search latency, or confusable-premise density.
- Budgets count attempted candidates, not matched Lean CPU time; tactic runtimes are not reported.
- Random results are seeded Monte Carlo estimates. The oracle only ranges over generated candidates.
- Structural routing measures whether a supplied patch is needed under this bounded catalog, not whether no direct Lean proof exists.

The next useful study should run the controller against real frozen theorem holes, extract successor states from Lean, retrieve from a full declaration index, and compare policies at matched Lean CPU budgets.

## Reproduce

```bash
lean-cache use .
lean-cache check-env
lean-cache build --wait .
python3 -m unittest discover -s tests -v
python3 -m jevlean.progress check-lean
python3 -m jevlean.progress metrics
```

Replay and Lean verification need no network credential. Do not run `freeze` unless intentionally defining a new experiment.
