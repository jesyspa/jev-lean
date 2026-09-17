# jev-lean

Reproducible experiments on Jev-guided Lean proof actions.

The current real-transition study samples exactly 100 next tactics from pinned Sipser proof scripts, reconstructs every pre-state in Lean, asks Jev to choose among bounded shuffled candidates, and Lean-checks every miss under explicit continuation criteria. Jev exactly matched 57/100 recorded actions; one additional miss was a verified alternative.

See [NEXT_STEP_100_REPORT.md](NEXT_STEP_100_REPORT.md) for the real-transition study. [REPORT.md](REPORT.md) covers the earlier synthetic progress benchmark, and [DESIGN.md](DESIGN.md) describes the controller boundary. [PANTOGRAPH_SPIKE.md](PANTOGRAPH_SPIKE.md) records the bounded backend feasibility verdict; the full search controller is not implemented.

## Reproduce the recorded experiment

Replay needs no API credential:

```bash
lean-cache use .
lean-cache check-env
lean-cache build --wait .
python3 -m unittest discover -s tests -v
python3 -m jevlean.progress check-lean
python3 -m jevlean.progress metrics
python3 -m jevlean.next_step metrics
```

`check-lean` verifies the generated action matrix serially in one Lean process. `metrics` validates every frozen request and content-addressed response before reporting results.

The committed prompt freeze predates evaluation. Do not replace it for ordinary replay. To define and run a new experiment, edit the public benchmark, run `python3 -m jevlean.progress freeze`, commit that freeze before making calls, and then run `python3 -m jevlean.progress live` with `TYPESAFE_API_KEY` in the environment. The client never records headers or credentials.

## Main files

- `data/next-step-100.json`: 100 frozen real source transitions, states, and shuffled options.
- `data/next-step-100-prompt-freeze.json`: final request hashes committed before evaluation.
- `artifacts/next-step-100-jev-1.13.0-trace.jsonl`: safe final live trace.
- `artifacts/next-step-100-lean-outcomes.json`: mechanical classification of every Jev miss.
- `artifacts/next-step-100-metrics.json`: final metrics and per-case results.
- `jevlean/next_step.py`: sampling, state reconstruction, prompts, Lean checks, and replay.
- `data/progress-benchmark.json`: public cases, generation metadata, continuations, and budgets.
- `data/library-index.json`: compact frozen declaration index used by bounded retrieval.
- `data/progress-prompt-freeze.json`: pre-evaluation request hashes.
- `artifacts/progress-lean-outcomes.json`: Lean-verified immediate and bounded outcomes.
- `artifacts/progress-jev-1.13.0-trace.jsonl`: safe content-addressed TypeSafe responses.
- `artifacts/progress-metrics.json`: replayed headline and per-case metrics.
- `jevlean/progress.py`: generation, retrieval, API, replay, Lean checking, and baselines.

The earlier immediate-closure prototype remains reproducible through `jevlean.experiment` and its original artifacts.
