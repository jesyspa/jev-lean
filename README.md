# jev-lean

A reproducible Jev-first Lean experiment that ranks verified next steps toward a proof.

The current benchmark generates concrete actions from local hypotheses, local inductive variables, structural templates, and bounded retrieval over a frozen Mathlib declaration index. Lean checks immediate closure and bounded multi-step continuation. Held-out comparisons cover Jev, Aesop-first, deterministic, seeded-random, and candidate-oracle orderings. Useful-lemma selection and structural-helper routing are evaluated separately.

See [REPORT.md](REPORT.md) for results, cost, and limitations. [DESIGN.md](DESIGN.md) describes the controller boundary.

## Reproduce the recorded experiment

Replay needs no API credential:

```bash
lean-cache use .
lean-cache check-env
lean-cache build --wait .
python3 -m unittest discover -s tests -v
python3 -m jevlean.progress check-lean
python3 -m jevlean.progress metrics
```

`check-lean` verifies the generated action matrix serially in one Lean process. `metrics` validates every frozen request and content-addressed response before reporting results.

The committed prompt freeze predates evaluation. Do not replace it for ordinary replay. To define and run a new experiment, edit the public benchmark, run `python3 -m jevlean.progress freeze`, commit that freeze before making calls, and then run `python3 -m jevlean.progress live` with `TYPESAFE_API_KEY` in the environment. The client never records headers or credentials.

## Main files

- `data/progress-benchmark.json`: public cases, generation metadata, continuations, and budgets.
- `data/library-index.json`: compact frozen declaration index used by bounded retrieval.
- `data/progress-prompt-freeze.json`: pre-evaluation request hashes.
- `artifacts/progress-lean-outcomes.json`: Lean-verified immediate and bounded outcomes.
- `artifacts/progress-jev-1.13.0-trace.jsonl`: safe content-addressed TypeSafe responses.
- `artifacts/progress-metrics.json`: replayed headline and per-case metrics.
- `jevlean/progress.py`: generation, retrieval, API, replay, Lean checking, and baselines.

The earlier immediate-closure prototype remains reproducible through `jevlean.experiment` and its original artifacts.
