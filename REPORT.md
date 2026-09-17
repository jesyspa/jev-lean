# Jev-first Lean proof-search experiment

## Result

This small study supports continuing with a Jev-first controller, but not deploying one yet. Jev selected a Lean-verified successful action on 11 of 12 synthetic goals. It identified all three states labeled as needing an auxiliary declaration and all eight useful lemmas in bounded candidate sets. It did not identify any of the three states labeled for direct LLM tactic fallback.

The practical architecture is therefore a probability-ranked catalog with Lean verification, followed by separate direct-proof and structural-patch fallback paths. A single greedy Jev choice is insufficient.

## Setup

The benchmark uses public synthetic goals modeled on proof shapes observed in the public `sipser` Lean formalization: direct premises, definitional equality, simplification, arithmetic, extensionality, propositional reasoning, list induction, closure lifting, accumulator generalization, and one-hole contexts. No `sipser` source text was sent to TypeSafe.

- Lean: `leanprover/lean4:v4.30.0`
- Mathlib: `v4.30.0`, commit `c5ea00351c28e24afc9f0f84379aa41082b1188f`
- Requested model: `jev-1.13.0`
- Resolved model in every response: `jev-1.13.0`
- Live requests: 28, each containing one independent `Choice`
- Recorded input/output tokens: 25,783 / 3,529
- Sequential API latency: 15.107 seconds total, 0.540 seconds/request mean
- Estimated Jev cost at the documented $0.042/M input-token price: $0.001083

The experiment made one recorded pass. It did not tune prompts against a held-out split.

## Tactic selection

Code enumerated the same 20-action catalog for every goal. The catalog includes premise closure, reflexivity, simplification, Aesop, Omega, numeric normalization, constructors, extensionality, function extensionality, contradiction, propositional automation, linear arithmetic, ring normalization, decision procedures, list cases/induction, conjunction construction, and an existential witness. Two explicit options route to direct-LLM or helper-declaration fallback.

Every one of the 240 goal/action pairs was checked by Lean in one serial process. A tactic counts as successful only when it closes the theorem.

| Top-1 policy | Solved | Rate |
| --- | ---: | ---: |
| Jev choice | 11/12 | 91.7% |
| Always `aesop` | 10/12 | 83.3% |
| Fixed first action (`assumption`) | 1/12 | 8.3% |
| Seeded random action | 3/12 | 25.0% |
| Uniform-random expectation from the verified matrix | 2.65/12 | 22.1% |
| Oracle: any catalog action | 12/12 | 100% |

Jev's failure was set distributivity. It selected `ext <;> simp`, which leaves a propositional distributivity goal; `aesop` was the only successful catalog action. The selected answer had probability 0.62 and confidence 0.59. Conversely, the correct list-length selection had confidence 0.37 in the committed pass, so confidence is not a correctness certificate or an obvious universal gate.

The catalog includes case-specific templates such as induction on a list named `xs`. A real controller must instantiate templates from local syntax and try actions in probability order. The 12/12 oracle result shows that Jev's one miss can be recovered without invoking an LLM.

## Fallback recognition

Eight labeled routing states tested three controller outcomes: use the listed catalog, request a direct LLM tactic, or request an auxiliary lemma/definition.

| Gold route | Correct | Recall |
| --- | ---: | ---: |
| Listed action | 2/2 | 100% |
| Direct LLM tactic | 0/3 | 0% |
| Helper declaration | 3/3 | 100% |
| Overall | 5/8 | 62.5% |

An always-`listed_action` baseline scores 2/8 (25%). Jev correctly recognized the accumulator-generalization, closure-lifting, and compositional-context cases as structural. It routed all three direct-fallback cases to the listed catalog, including one wrong decision with confidence 0.85.

These labels are author judgments, not outcomes from a complete search. One named-hypothesis case may in fact be solvable by broad automation, which would make the direct-fallback label debatable. The next benchmark must define routing labels operationally: exhaust a fixed catalog and budget, then distinguish a verified direct generated action from a verified helper patch.

No generative LLM was invoked in this pass. The experiment measures the proposed gate, not fallback solve quality, provider quality, or end-to-end cost.

## Bounded lemma selection

Each of eight tasks gave Jev five named lemmas with short signatures.

| Top-1 policy | Correct | Rate |
| --- | ---: | ---: |
| Jev choice | 8/8 | 100% |
| Token-overlap heuristic | 4/8 | 50% |
| First candidate | 2/8 | 25% |
| Seeded random candidate | 2/8 | 25% |

Every Jev lemma choice had reported confidence 1.0. This is a sanity check, not evidence for realistic premise retrieval: the correct signature closely matches the goal, candidate sets are small, and distractors are easy. A useful follow-up must use bounded candidates returned by actual environment search, with confusable declarations and proof verification after selection.

## Trace safety and reproduction

`artifacts/jev-1.13.0-trace.jsonl` stores the exact raw response body, its SHA-256 hash, request hash, public case ID, model metadata, token usage, and latency. It does not store request headers, API keys, or external source payloads. Requests are reconstructed from `data/benchmark.json`; `verify_trace` rejects stale request hashes, malformed probabilities, changed raw responses, and model-metadata mismatches.

`artifacts/lean-outcomes.json` contains only public case/action IDs and verified booleans. Replay needs no network access:

```bash
lean-cache use .
lean-cache check-env
lean-cache build --wait .
python3 -m unittest discover -s tests -v
python3 -m jevlean.experiment check-lean
python3 -m jevlean.experiment metrics
```

The Lean matrix checker uses one process to fit a modest machine.

## Limitations

- Twelve tactic goals, eight routing states, and eight lemma tasks are too small for confidence intervals or threshold calibration.
- The goals are synthetic and simpler than much of `sipser`.
- The same benchmark informed question design and evaluation.
- Only one live pass is reported; model repeatability was not measured.
- Top-1 closure ignores useful successor states and multi-step search.
- Routing labels were not established by exhaustive catalog and LLM runs.
- The fixed catalog does not yet generate terms from local hypotheses or variable names.
- The premise task does not test retrieval recall.
- No direct-LLM, helper-patch, or fully LLM-based solving baseline was run.
- Latency is sequential API latency and excludes Lean startup/build time.

## Recommended next architecture

Build a serial external controller that generates concrete actions from each local context, asks Jev for one `Choice`, and executes actions in descending probability until one closes or yields a useful verified successor. Keep a bounded best-first queue rather than committing greedily. Use deterministic goal-count and duplicate-state checks outside Jev.

Treat `llm_tactic_fallback` and `llm_helper_fallback` as separate contracts. Direct fallback returns tactics or terms. Structural fallback returns private helper declarations plus a revised proof and is compiled in a sandbox module. Also trigger direct fallback after the verified catalog budget is exhausted, because this study shows that Jev can over-predict catalog coverage.

The next decision gate is a held-out benchmark of at least 100 real theorem holes with matched Lean CPU and model budgets. Measure end-to-end verified solve rate for deterministic automation, Jev-ranked catalog search, direct LLM solving, and Jev-first search with both fallback paths. Add realistic premise retrieval and verify every selected premise through an executed action.
