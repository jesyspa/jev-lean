# Bounded search calibration

Use this report when changing the default `JevLean.Search.Config` limits or a catalogue generator. The reproducible measurement is `Calibration.lean`; `python3 -m jevlean.calibration` refreshes `artifacts/search-calibration-metrics.json`, and `python3 -m jevlean.calibration --check` verifies the fixture during tests.

## Fixture and comparator

The fixture runs six frozen goals with the deterministic identity ranker. The ranker seam exercises the scheduler and counts rank invocations without a credential or network request. Each successful path is replayed from the original tactic state. The `pre_work` comparator preserves the core scheduler and disables retrieval, rewrite, unfolding, local-application, added structural bounds, and transpositions. `accumulated` uses `Config` defaults.

The goals include a plain local close plus fixture retrieval, equality normalization, definition-head, local application, and structural-destructuring states. They are representative proof states, not imported source theorems. Existing committed next-step and progress benchmarks remain frozen ranking studies with different candidate formats, so their recorded scores are not combined with this end-to-end search result.

## Recorded result

| Mode | Solved / 6 | Replayed | Jev calls | Nodes | Transitions | Wall time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Pre-work comparator | 4 | 4 | 7 | 7 | 66 | 132 ms |
| Accumulated defaults | 6 | 6 | 8 | 8 | 100 | 2,871 ms |

The two additional closes are the equality-rewrite and structural-destructuring fixtures. Retrieval, unfolding, and local application remain replayable in their fixtures; their shapes are already definitionally or structurally accessible to the core catalogue, so this measurement does not attribute an independent solve gain to them. Accumulated generation admits 22 successors and suppresses 12 canonical duplicates; the comparator admits 9 successors and has no transposition table.

Wall time is machine-dependent and includes candidate elaboration over the full Lean environment. It is reported as an observed latency rather than a stable threshold. The solve gain does not justify increasing any default budget: the recorded accumulated run already adds about 2.7 seconds while expanding one additional node and making one additional ranker invocation. Default limits therefore remain unchanged.

## Interpretation

This is a small regression fixture, not an estimate of Mathlib-scale autonomous theorem proving. It establishes bounded candidate execution, scheduler accounting, and source replay for the accumulated generators. The committed `artifacts/search-calibration-metrics.json` is a concrete run; rerun the command above on the target machine when comparing latency.
