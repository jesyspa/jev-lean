# Jev next-step prediction on 100 real Lean transitions

## Result

Jev exactly reproduced the recorded next tactic on **57 of 100** frozen proof states. One miss was a Lean-verified alternative, so semantic acceptability was **58 of 100** under the study's deliberately strict continuation criterion.

This is evidence that Jev can identify many author-recorded next steps in real proof scripts from a bounded option set. It is not a 58% theorem-proving success rate. Exact match measures imitation, while semantic acceptability only credits an alternative when Lean verifies it with the recorded remainder of the proof.

## Headline metrics

| Policy or measure | Accepted / 100 | Rate |
| --- | ---: | ---: |
| Jev exact match | 57 | 57% |
| Jev semantic acceptability | 58 | 58% |
| Seeded-random exact match | 15 | 15% |
| Deterministic exact match | 6 | 6% |
| Aesop-first exact match | 0 | 0% |
| Recorded-action oracle | 100 | 100% |

The random policy's analytic expectation was 10.519 exact matches; 15 is the result of the single committed seeded draw. The deterministic policy uses the fixed priority `rfl`, `simp`, `assumption`, `constructor`, `omega`, `aesop`, then lexical order. Aesop-first chooses `aesop` whenever it is offered, then uses the deterministic policy. These baselines measure exact imitation only. They were not given the post-hoc semantic continuation checks used to analyze Jev's misses. The oracle is the ceiling induced by always selecting the recorded option, not an unbounded proof-search oracle.

## Dataset

The benchmark is a deterministic, family-balanced sample of exactly 100 transitions from 28 files at Sipser revision `57ac584959f03a3e98c4decf04162cd3a1af6b59`. All cases use real theorem or lemma proof scripts. The source toolchain is Lean `v4.30.0` with Mathlib commit `c5ea00351c28e24afc9f0f84379aa41082b1188f`.

The eligible population consists of complete one-line tactics at the base indentation of proof blocks. Files over 30 KB, nested bullet/case actions, multiline layout openers, unbalanced syntax, and trailing combinators are excluded. This restriction makes action substitution and successor-state checking reproducible. It also creates an important sampling bias toward linear proof segments.

The sample contains 27 tactic families. The largest families—`classical`, `decide`, `unfold`, `obtain`, `rfl`, `exact`, and `rcases`—each contribute six cases. No source file contributes more than 15 cases.

For every case, the generator:

1. inserts an atomic custom trace tactic immediately before the sampled action;
2. elaborates the instrumented source against the lean-cache-backed Sipser build;
3. freezes the pretty-printed pre-state and its SHA-256 hash;
4. adds candidates from local hypotheses and variables, standard tactic families, structural tactics, and at most two declarations retrieved from the existing frozen compact library index;
5. deduplicates to at most 10 options and shuffles them with the committed seed.

Option sets contain 8–10 actions, with mean size 9.55. Every set contains the recorded action. A separate pre-evaluation validation executed each recorded action from its reconstructed prefix in Lean.

## Prompt and freeze

The question was:

> Which listed tactic is the best next action toward a complete proof of the theorem?

The prompt explicitly says to judge progress toward the whole proof, not immediate closure. It also says that a structural action creating useful successor goals can be preferable to an immediate-closing attempt.

The corrected dataset, option order, reconstructed states, and all 100 request hashes were committed in `de2232bc1216fa70c717ea406c98940ba13f4cfd` before the final calls. The dataset hash is `1670488f90e10076d967472f16a5e0dc66298ca8c7b5533722fec1f5ae02fe3e`. Replay rejects any mismatch.

An earlier 100-call pilot exposed that the initial eligibility rule admitted multiline/layout tactic openers and that the first isolated checker mishandled tactic focus. Those calls are excluded from every headline metric. Their safe trace, outcomes, and metrics are retained as `next-step-100-excluded-pilot-*`; the final dataset was re-frozen before its one live evaluation.

## Lean classification of every Jev miss

Jev missed the recorded option on 43 states. Each selected action was then inserted into the exact source prefix and executed in Lean. The committed criteria are ordered and mechanical:

1. **Invalid:** the prefix plus selected action fails before successor goals can be admitted.
2. **No progress:** Lean accepts the action, but its first printed successor state is byte-identical to the frozen pre-state.
3. **Verified alternative:** replacing the recorded action with Jev's action and retaining the recorded proof suffix compiles the theorem.
4. **Valid but unverified continuation:** Lean accepts the action and changes the state, but the unchanged recorded suffix does not compile the theorem.

| Miss classification | Count |
| --- | ---: |
| Invalid | 12 |
| No progress | 0 |
| Valid but unverified continuation | 30 |
| Verified alternative | 1 |

The verified alternative is `constructor` in `Sipser/Computability/Exercises/M1Traces.lean`, where the source used `unfold trace11 sourceTrace11`. Lean compiled the theorem after direct substitution and retention of the source suffix.

The 30 valid-but-unverified choices are not declared mathematically wrong. The study does not credit them because no machine-checked continuation was found under the frozen suffix criterion. This avoids author-intuition judgments but intentionally undercounts alternatives that require changing later tactics.

## Model, usage, cost, and traces

All 100 final requests asked for and resolved to **`jev-1.13.0`**.

- Input tokens: 82,956
- Output tokens: 10,495
- Sequential API latency: 63.945548 seconds
- Estimated input cost at the documented $0.042 per million input tokens: **$0.003484152**
- Actual billed currency amount: unavailable; responses report tokens but no monetary charge

The excluded pilot used 85,239 input and 10,630 output tokens, with estimated input cost $0.003580038. Total development plus final live usage was 168,195 input tokens and estimated input cost $0.007064190.

The JSONL traces contain request hashes, exact public response bodies and hashes, resolved model IDs, usage, and latency. They contain no API key, authorization header, or other credential. Lean outcomes retain the selected action, successor states, return codes, and classification.

## Limitations

- The sample is family-balanced rather than representative of the natural tactic frequency in Sipser.
- Restricting to base-indented one-line tactics excludes nested branches, term proofs, and many structurally difficult transitions.
- All cases come from one project and one source revision. LeanDojo Benchmark 4's `novel_premises` split is a strong external-validation target because it offers 2,000 traced Mathlib theorems from a separately pinned revision.
- The option generator uses lightweight parsing of pretty-printed local context. It does not query Lean's full environment for type-directed candidate synthesis.
- Library retrieval is capped at two candidates from the existing compact 32-declaration index. This does not measure Mathlib-scale retrieval.
- Exact match treats the source action as the target even when several next steps may be good.
- Semantic acceptability is a lower bound under one fixed continuation: a valid action receives no credit unless the unchanged recorded suffix proves the theorem.
- The no-progress test compares pretty-printed states byte-for-byte. It does not normalize definitional equality or proof-irrelevant context.
- Baselines are exact-imitation controls, not matched-budget semantic proof-search systems. In particular, Aesop-first's 0% exact score does not imply that `aesop` is never useful.
- This is one deterministic sample and one live pass through one model version. There are no confidence intervals over projects, samples, or repeated model calls.

## Reproduce

Replay of the committed final requests, responses, classifications, and metrics needs no API credential:

```bash
lean-cache use .
lean-cache check-env
lean-cache build --wait .
python3 -m unittest discover -s tests -v
python3 -m jevlean.next_step metrics
```

Reconstructing source states and rerunning Lean classifications additionally requires Sipser at the pinned revision, already built through lean-cache:

```bash
python3 -m jevlean.next_step verify-states --source-root /path/to/sipser
python3 -m jevlean.next_step check-selections --source-root /path/to/sipser
```

Do not run `freeze` or `live` for ordinary replay. `freeze` defines a new experiment, and `live` spends API calls.
