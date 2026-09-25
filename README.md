# JevLean

JevLean is an experimental Lean 4 tactic that asks the Jev model to rank possible proof steps. Lean checks each step and produces an ordinary proof suggestion when search succeeds. It is not a general-purpose automatic prover.

## Get started

Install Git, Python 3, and Lean via `elan` (https://lean-lang.org/install/). Then build the project:

```sh
git clone https://github.com/jesyspa/jev-lean.git
cd jev-lean
lake build
```

The project pins its Lean version in `lean-toolchain`; Lake downloads its dependencies.

To use `jev?`, get a TypeSafe API key. Start the local model broker in one terminal:

```sh
TYPESAFE_API_KEY=your_key python3 -m jevlean.model_broker
```

Create `Example.lean` in the repository:

```lean
import JevLean

example (P Q : Prop) (hp : P) (hq : Q) : P ∧ Q := by
  jev?
```

Run it in another terminal:

```sh
lake env lean Example.lean
```

If search finds a proof, Lean prints a `Try this` suggestion. Replace `jev?` with the suggested tactics so the proof checks without the broker. Model calls can incur charges. The broker binds to `127.0.0.1:8765` by default.

Proof states and candidate tactic descriptions are sent to TypeSafe for ranking. They may contain private theorem statements or local values. Use the tactic only on material you can share with that provider. The optional helper feature (`OPENROUTER_API_KEY` and `JEV_LLM_HELPERS=1`) also sends proof states to OpenRouter.

## Run tests

```sh
./test.sh
```

The default test suite uses a local deterministic broker; it needs no API key or paid model calls. This repository also includes benchmark scripts and recorded experimental data, but their results do not establish a general theorem-solving success rate.

Licensed under Apache 2.0; see `LICENSE`. Third-party data notices are in `NOTICE`.
