# jev-lean

A reproducible Jev-first Lean proof-search experiment.

The prototype enumerates a fixed catalog of 20 standard Lean actions, asks Jev to select one, and verifies every action with Lean. Separate tasks test whether Jev routes a state to direct LLM assistance or an auxiliary declaration, and whether it selects a useful lemma from a bounded candidate set.

Results and limitations are in [REPORT.md](REPORT.md). The recommended controller architecture is in [DESIGN.md](DESIGN.md).

## Reproduce the recorded experiment

The committed trace uses `jev-1.13.0`. Replay and metrics need no API key:

```bash
lean-cache use .
lean-cache check-env
lean-cache build --wait .
python3 -m unittest discover -s tests -v
python3 -m jevlean.experiment check-lean
python3 -m jevlean.experiment metrics
```

`check-lean` verifies the complete 12 × 20 action matrix in one Lean process. It is intentionally serial to limit memory use.

To replace the live trace, put `TYPESAFE_API_KEY` in the environment and run:

```bash
python3 -m jevlean.experiment live
```

The standard-library HTTP client sends only the public synthetic benchmark. It never logs request headers or credentials.

## Files

- `data/benchmark.json`: public states, action catalog, routing labels, and lemma candidates.
- `artifacts/lean-outcomes.json`: Lean-verified action matrix.
- `artifacts/jev-1.13.0-trace.jsonl`: content-addressed raw TypeSafe responses.
- `jevlean/experiment.py`: API client, Lean checker, baselines, trace validation, and metrics.
- `JevLean.lean`: pinned Mathlib environment and benchmark helper definition.
