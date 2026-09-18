# jev-lean

Experiments on Jev-guided Lean proof search. Lean generates or executes concrete actions and remains the sole correctness oracle; Jev is evaluated as a ranker of promising proof steps.

`jev?` is a deterministic FIFO bounded Lean-native successor search. Each frontier node retains a restorable tactic state, ordered goals, replayable path, depth, and cost. Expansion applies `intro`, `constructor`, `left`/`right`, suitable-hypothesis `cases`, suitable-local `induction`, local `exact`/`apply`, and bounded closing automation to the first goal while preserving sibling order. A persistent localhost Python broker ranks concrete actions using the focused goal, pending sibling goals, and path context; it keeps one HTTPS connection to TypeSafe where the server permits reuse. `jev?` requires a nonempty `TYPESAFE_API_KEY` and a reachable broker; either missing prerequisite is an explicit tactic error. Broker responses are bounded JSON frames and action order is validated before use. Successful rankings are cached in the Lean process, so incremental re-elaboration of the same state reuses them. `aesop` remains a normal rankable candidate. A closing path is replayed and emitted as raw ordinary tactic source through `Try this`.

Start the broker before invoking `jev?`:

```bash
TYPESAFE_API_KEY=... python3 -m jevlean.rank_broker
```

It listens only on `127.0.0.1:8765`; set `JEV_RANK_BROKER_PORT` in Lean's environment and pass the same `--port` to use another port. The broker exits on startup without a key and is intentionally not started or stopped by Lean, so its credentialed lifecycle is explicit. Each connection carries exactly one newline-delimited JSON request and is closed after one response; both sides enforce a 1 MB frame limit. The remaining theorem wall allowance is sent as the rank deadline, and Lean also abandons a broker operation at that deadline.

The default practical ledger is depth 6, path cost 6, 64 visited frontier nodes, 256 attempted catalogue transitions, 16 Jev calls, and 10 seconds of wall time. Wall time is checked between Lean transitions and Lean tactic execution remains subject to Lean's enclosing heartbeat limit. Raising this deadline accommodates remote ranking latency; it does not solve search scheduling, branching, retrieval, rewriting, generalization, or generated helper proofs. Local suitability is deliberately shallow (`cases` on propositions and `induction` on non-proposition locals), and the catalogue has no premise retrieval, rewriting, generalization, or generated helper proofs.

## Results so far

| Study | Result | What it establishes | Main limitation |
|---|---:|---|---|
| Initial synthetic catalogue | Jev selected a successful tactic on 11/12 goals; always-`aesop` solved 10/12 | A live Jev call can rank a small verified tactic catalogue | Small synthetic set with fixed actions |
| Synthetic progress benchmark | Jev found a frozen useful action within three attempts on 14/15 goals; Aesop-first found 13/15 | Jev can rank some non-closing structural steps and compact retrieval candidates | The 15 goals and bounded continuations were author-written |
| Sipser next-step study | 57/100 exact matches; 58/100 under a strict Lean-checked continuation criterion | Jev often recognizes an author-recorded next tactic among 8–10 options on real proof states | Family-balanced linear tactics from one project; not theorem solve rate |
| Pantograph spike | 9/10 feasibility gates passed | Branching, multi-goal execution, isolated helper lineages, recovery, and replay are feasible | `rw?` ignored the intended timeout and exceeded a 180-second wall limit |

The strongest current evidence is next-action ranking, not autonomous proof search. None of these studies measures end-to-end theorem solve rate, Mathlib-scale retrieval, or LLM-generated helper success.

Detailed methods and caveats:

- [NEXT_STEP_100_REPORT.md](NEXT_STEP_100_REPORT.md)
- [REPORT.md](REPORT.md)
- [PANTOGRAPH_SPIKE.md](PANTOGRAPH_SPIKE.md)

## Design status

The intended user interface is a Lean suggestion tactic:

```lean
theorem example ... := by
  jev?
```

It should search, check the result in Lean, and offer ordinary Lean source through `Try this`. Users should commit the generated proof rather than leave network-dependent search in normal builds.

Two implementation designs remain plausible:

1. **Lean-native search:** run action generation, tactic-state branching, and search in `TacticM`; use an external broker only for credentials and model calls.
2. **External search:** run the scheduler outside Lean and use Pantograph to execute actions in supervised Lean processes.

A hybrid can expose `jev?` while searching in an isolated external Lean worker. [DESIGN.md](DESIGN.md) compares these designs and records the shared constraints.

Independent of implementation language:

- Lean and Mathlib supply tactic execution, suggestions, automation, and checking.
- Jev ranks concrete verified alternatives; it does not invent Lean code.
- Generative LLMs are fallback behavior for exhausted tactical search or complete helper patches.
- Every accepted proof is replayed from source in a fresh Lean process.
- Credentials remain outside generated-code workers and committed traces.
- Evaluations freeze tasks and compare policies under explicit resource budgets.

## Reproduce committed results

Use the project cache wrapper for Lean builds:

```bash
lean-cache use .
lean-cache check-env
lean-cache build --wait .
python3 -m unittest discover -s tests -v
python3 -m jevlean.progress metrics
python3 -m jevlean.rank < rank-request.json
TYPESAFE_API_KEY=... python3 -m jevlean.rank_broker
python3 -m jevlean.next_step metrics
```

Replay uses committed, credential-free traces. Live commands require a TypeSafe API key and intentionally create a new experiment.
