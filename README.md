# jev-lean

Experiments on Jev-guided Lean proof search. Lean generates or executes concrete actions and remains the sole correctness oracle; Jev is evaluated as a ranker of promising proof steps.

`jev?` is a deterministic rank-guided bounded Lean-native successor search. Each frontier node retains a restorable tactic state, ordered goals, replayable path, depth, and cost. Expansion applies `intro`, `constructor`, `left`/`right`, bounded proposition and inductive-data `cases`, bounded inductive `induction` including target-relevant accumulator generalization, checked conjunction/existential destructuring with fresh `jev_h` names, checked existential witnesses from local terms, local `exact`/`apply`, matched local equality rewrites in both directions, matched `simp only` normalization lemmas, goal-head and hypothesis-head definition unfolds, focused-goal-and-context global named-lemma `exact`/`apply` retrieval, and bounded closing automation to the first goal while preserving sibling order. Rewrite and definition-unfold generation have independent candidate bounds; each unfold is checked before ranking and a definition can be unfolded only once per search path. Retrieval scores declaration conclusions by constants shared with the focused goal and local context, retains a deterministic bounded name list, and executes every rendered candidate in the current tactic state before it reaches Jev; unavailable or ill-typed names never enter the ranking request. A persistent localhost Python broker ranks concrete actions using the focused goal, pending sibling goals, and path context; it keeps one HTTPS connection to TypeSafe where the server permits reuse. `jev?` requires a reachable broker. The broker requires a nonempty `TYPESAFE_API_KEY` and reports credential failures to the tactic. Broker responses are bounded JSON frames and action order is validated before use. Successful rankings are cached in the Lean process, so incremental re-elaboration of the same state reuses them. `aesop` remains a normal rankable candidate. A closing path is replayed and emitted as raw ordinary tactic source through `Try this`.

## Optional LLM helper cuts

Set `JEV_LLM_HELPERS=1` to enable a separate helper-state meta-action. Its deterministic gate estimates helper need from the rendered state: 20% base, +20% per sibling, +55% for quantified/existential goals, and +20% for implications. The default threshold is 85%; only states at or above it make one cached request. Equivalent focused goal plus ordered siblings share a canonical identity, so revisits do not repeat a request. Each request is capped at 2 seconds, four structured propositions, and two admitted cuts.

Run the localhost helper broker with a nonempty `OPENROUTER_API_KEY`; `JEV_HELPER_BROKER_PORT` selects its default-8766 port and `JEV_HELPER_MODEL` selects its default `openai/gpt-4o-mini` model. The endpoint is `https://openrouter.ai/api/v1/chat/completions`. Lean warns once per `jev?` invocation when helpers are enabled but the key is absent. Provider failures are ignored and ordinary candidates remain available.

The provider returns proposition/rationale pairs, never Lean tactics. Lean parses and elaborates each proposition in the live context, rejects malformed, unavailable, non-`Prop`, unchanged, and `sorry`/`admit` proposals, then includes surviving cuts in Jev's normal ranking request. An admitted `H` is `refine (let h : H := ?_; ?_)`: search must prove `H` from the original context and then prove the original goal with `h : H`; no provider output is trusted as a proof.

Start the ranking broker before invoking `jev?`:

```bash
TYPESAFE_API_KEY=... python3 -m jevlean.rank_broker
```

It listens only on `127.0.0.1:8765`; set `JEV_RANK_BROKER_PORT` in Lean's environment and pass the same `--port` to use another port. The broker exits on startup without a key and is intentionally not started or stopped by Lean, so its credentialed lifecycle is explicit. Each connection carries exactly one newline-delimited JSON request and is closed after one response; both sides enforce a 1 MB frame limit. The remaining theorem wall allowance is sent as the rank deadline, and Lean also abandons a broker operation at that deadline.

The scheduler fingerprints every executed successor from its ordered rendered goals and retains a bounded transposition table keyed by that canonical state. Equal-cost duplicate successors and cycles are suppressed before they enter the frontier; a lower-cost route replaces the retained route. This applies equally to ordinary and helper-cut actions, and the canonical identity also keys helper-provider caching. Search metrics expose expanded nodes, attempted transitions, admitted and duplicate successors, repeated action families, table occupancy, and wall time. The representative duplicate-outcome fixture is recorded in `artifacts/search-diversity-metrics.json`; it found no evidence supporting a family quota, so selection preserves all canonically distinct states.

The default practical ledger is depth 6, path cost 6, 64 visited frontier nodes, a 128-state transposition table, 256 attempted catalogue transitions, 16 Jev calls, 10 seconds of wall time, and 24 retrieved global names per focused goal. The retrieval bound is applied before ranking and admits at most two checked actions (`exact` and `apply`) per name. Structural generation is independently capped at two proposition cases, two data cases, two inductions, one target-relevant generalizing induction, two existential witnesses, and two conjunction/existential destructures. Every optional structural action is executed before ranking; data actions require an inductive local, witness actions require an existential target, and generalized locals must occur in that target. Fresh destructuring names avoid existing context names, so replay and descendant generation remain stable. Wall time is checked between Lean transitions and Lean tactic execution remains subject to Lean's enclosing heartbeat limit. Rewrite generation is restricted to checked equality rewrites and checked simp-only normalizations, while unfold generation only considers definition constants at focused heads.

## Results so far

| Study | Result | What it establishes | Main limitation |
|---|---:|---|---|
| Initial synthetic catalogue | Jev selected a successful tactic on 11/12 goals; always-`aesop` solved 10/12 | A live Jev call can rank a small verified tactic catalogue | Small synthetic set with fixed actions |
| Synthetic progress benchmark | Jev found a frozen useful action within three attempts on 14/15 goals; Aesop-first found 13/15 | Jev can rank some non-closing structural steps and compact retrieval candidates | The 15 goals and bounded continuations were author-written |
| Sipser next-step study | 57/100 exact matches; 58/100 under a strict Lean-checked continuation criterion | Jev often recognizes an author-recorded next tactic among 8–10 options on real proof states | Family-balanced linear tactics from one project; not theorem solve rate |
| Pantograph spike | 9/10 feasibility gates passed | Branching, multi-goal execution, isolated helper lineages, recovery, and replay are feasible | `rw?` ignored the intended timeout and exceeded a 180-second wall limit |
| Bounded search calibration | Accumulated defaults solved and replayed 6/6 frozen goals; the feature-disabled comparator solved 4/6 | Generated rewrite and structural paths add verified closes under the current ledger | Six representative states with a deterministic ranker are not a Mathlib-scale solve-rate estimate |

The strongest current evidence is next-action ranking, not autonomous proof search. None of these studies measures end-to-end theorem solve rate, Mathlib-scale retrieval, or LLM-generated helper success.

The external end-to-end adapter, frozen 50-task LeanDojo Benchmark 4 pilot, leakage checks, and exact toolchain blocker are documented in [BENCHMARK4.md](BENCHMARK4.md). [EXTERNAL_BENCHMARKS.md](EXTERNAL_BENCHMARKS.md) compares current Lean 4 alternatives and recommends the next pilot and standard comparison.

Detailed methods and caveats:

- [NEXT_STEP_100_REPORT.md](NEXT_STEP_100_REPORT.md)
- [REPORT.md](REPORT.md)
- [PANTOGRAPH_SPIKE.md](PANTOGRAPH_SPIKE.md)
- [CALIBRATION_REPORT.md](CALIBRATION_REPORT.md)

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
python3 -m jevlean.calibration --check
```

Replay uses committed, credential-free traces. Live commands require a TypeSafe API key and intentionally create a new experiment.
