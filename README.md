# jev-lean

JevLean is a research prototype for model-ranked Lean 4 proof search. The `jev?` tactic builds and checks a bounded catalogue of Lean actions, asks Jev to rank them, explores restorable tactic states, replays a successful path, and emits ordinary tactic source through `Try this`. Lean and Mathlib remain the proof checker.

The tactic is implemented and covered by offline replay tests. The repository also contains frozen ranking studies, a six-goal search calibration, and external benchmark adapters. It is not a production prover, and the committed studies do not establish Mathlib-scale retrieval or end-to-end solve rates.

## Prerequisites and setup

Install Git, Python 3, and Lean through `elan`: https://lean-lang.org/install/

A normal setup uses Lake directly and does not require the host-specific build cache:

```bash
git clone https://github.com/jesyspa/jev-lean.git
cd jev-lean
lake build
```

The pinned toolchain is in `lean-toolchain`; Lake fetches the pinned Mathlib and Pantograph dependencies. Bubblewrap (`bwrap`) is required only by the external benchmark and Pantograph isolation runners on Linux.

## Use `jev?`

Create `Example.lean` in the repository:

```lean
import JevLean

example (P Q : Prop) (hp : P) (hq : Q) : P ∧ Q := by
  jev?
```

In one terminal, start the localhost broker with a TypeSafe API credential:

```bash
TYPESAFE_API_KEY=... python3 -m jevlean.model_broker
```

Then elaborate the file in another terminal:

```bash
lake env lean Example.lean
```

A successful run prints a `Try this` proof. Copy that ordinary Lean proof into source rather than making routine builds depend on the broker. Running this example makes a provider request and may incur provider charges.

The broker listens only on `127.0.0.1:8765`. Use `--port` and the matching `JEV_MODEL_BROKER_PORT` to select another port. `jev?` requires the broker's rank capability, supplied by `TYPESAFE_API_KEY`. Set `OPENROUTER_API_KEY` and `JEV_LLM_HELPERS=1` to enable optional helper-cut proposals; `JEV_HELPER_MODEL` defaults to `openai/gpt-4o-mini`. Credentials stay in the broker process and are not sent to Lean workers.

For ranking, Lean sends the rendered focused proof state, ordered sibling goals, the current tactic path, candidate identifiers, candidate tactic descriptions, and a deadline to the local broker. The broker forwards that payload to TypeSafe. Helper requests send the rendered focused state and sibling-state identity to OpenRouter. These strings can contain theorem statements, local hypotheses, names, and values from the active proof state. Do not enable provider calls for source or proof states that you cannot disclose to the configured services. Broker requests and responses are newline-delimited JSON frames bounded to 1,000,000 bytes.

## Credential-free verification

Run the fast offline suite:

```bash
./test.sh
```

It builds `JevLean`, runs Lean tactic and broker tests, Python unit tests, fresh-source replay regressions, and checks the committed synthetic metrics. It uses a deterministic local broker and makes no paid calls. The suite checks that tracked files are unchanged. If `lean-cache` is installed it may print host-cache status or cache-miss warnings; those are not project diagnostics. Lean, Python, or replay warnings and failures are project results and should be investigated.

The historical Pantograph feasibility spike is slower and optional:

```bash
RUN_PANTOGRAPH_SPIKE=1 ./test.sh
```

It can take several minutes because it starts isolated Lean workers. The miniF2F and LeanDojo runners are separate benchmark workflows with pinned environments; see [MINIF2F.md](MINIF2F.md) and [BENCHMARK4.md](BENCHMARK4.md).

## Evidence and limitations

- [REPORT.md](REPORT.md) records the synthetic ranking studies.
- [CALIBRATION_REPORT.md](CALIBRATION_REPORT.md) records bounded search calibration.
- [PANTOGRAPH_SPIKE.md](PANTOGRAPH_SPIKE.md) preserves the external-worker feasibility evidence.
- [EXTERNAL_BENCHMARKS.md](EXTERNAL_BENCHMARKS.md) compares the benchmark protocols.
- [DESIGN.md](DESIGN.md) describes the implemented boundaries and remaining design limits.

The strongest evidence is controlled next-action ranking and small bounded search. The current retrieval policy is bounded and heuristic. Provider availability, latency, and ranking quality affect `jev?`. Tactic execution is bounded cooperatively inside Lean, so a Lean tactic that does not yield can still hurt editor responsiveness. Optional helper propositions are untrusted: Lean parses and checks them, and search must prove both the helper and original goal.

## License and third-party data

The project is licensed under Apache License 2.0; see [LICENSE](LICENSE). [NOTICE](NOTICE) records the pinned miniF2F and Mathlib-derived data included in this repository. The miniF2F manifest contains ported statements, not upstream proof bodies or docstrings.
