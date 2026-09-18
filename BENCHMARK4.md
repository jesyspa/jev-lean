# LeanDojo Benchmark 4 pilot

Use this path to measure end-to-end `jev?` solving on the official LeanDojo Benchmark 4 `novel_premises/test` split.

## Frozen inputs

`data/leandojo-benchmark4-novel-premises-pilot.json` fixes 50 repository-owned test tasks. The selection takes the first 50 `Mathlib/` tasks ordered by SHA-256 of `file_path`, a NUL byte, and `full_name`. The manifest fixes:

- Zenodo record 12740403, dataset version v10
- archive MD5 `25e1ee60cd8925b9d2e8673ddcc34b4c`
- test export SHA-256 `6c001d1cacd141a4301965fa03cb3c756c19aa09fb30daf9cb2ccb48396ff3fe`
- Mathlib commit `29dcec074de168ac2bf835a77ef68bbe069194c5`
- Lean toolchain `leanprover/lean4:v4.10.0-rc1`

The official export is open and needs no credential:

```bash
python3 -m jevlean.benchmark4 acquire --output /path/to/data
```

Clone and prepare the benchmark checkout separately:

```bash
git clone https://github.com/leanprover-community/mathlib4 /path/to/mathlib4-benchmark
cd /path/to/mathlib4-benchmark
git checkout 29dcec074de168ac2bf835a77ef68bbe069194c5
lake update
lake exe cache get
```

## Leakage boundary

The runner reads only the frozen task identity, source path, declaration name, and source range. It does not read `traced_tactics` during materialization or execution. The environment audit rejects a changed revision or any working-tree change to a pilot source file.

For each task, `jevlean.benchmark4::materialize_task` copies the source prefix before the target, retains the target statement, replaces its proof, and omits the rest of the file. The target constant is not in Lean's environment while its replacement proof is elaborated. The original imports expose only declarations available before the original file; `JevLean` supplies the search tactic. A bubblewrap process gets a hidden home and a sanitized file mounted over the original target source path. Credential variables are absent from search and replay workers.

A separate fresh Lean process replays generated ordinary tactic source without importing `JevLean`. This rejects proofs that depend on search-infrastructure declarations or extra tactic imports. The per-task deadline covers search and replay together. Process-group termination enforces the deadline outside Jev's internal search ledger.

## Run

The search project must use the benchmark's exact Lean and Mathlib revisions and provide a `JevLean` module with the `jev_benchmark?` tactic. Audit before running:

```bash
python3 -m jevlean.benchmark4 audit \
  --search-project /path/to/compatible-jevlean \
  --mathlib-root /path/to/compatible-jevlean/.lake/packages/mathlib
```

Run or resume the pilot:

```bash
python3 -m jevlean.benchmark4 run \
  --search-project /path/to/compatible-jevlean \
  --mathlib-root /path/to/compatible-jevlean/.lake/packages/mathlib \
  --output /path/to/results \
  --timeout 30
```

Each completed task is written atomically to `results/tasks/<id>.json`. Existing matching task artifacts are skipped. `results/summary.json` records solve, failure, and timeout counts. Task artifacts include verified status, generated proof, fresh-replay status, wall and CPU time, search metrics, Jev/helper usage, leakage hashes, and bounded process output.

Live Jev ranking still uses the localhost rank broker. Start it outside the sandbox. The Lean worker receives the broker port but no provider credential. Set `JEV_LLM_HELPERS=1` and run the helper broker separately to measure helper use.

## Current compatibility blocker

The current JevLean search project uses Lean `v4.30.0` and Mathlib `c5ea00351c28e24afc9f0f84379aa41082b1188f`. Benchmark 4 uses Lean `v4.10.0-rc1` and Mathlib `29dcec074de168ac2bf835a77ef68bbe069194c5`. Oleans and metaprogram APIs are not compatible across these revisions. The current shared Mathlib tree also differs from the frozen pilot sources. The runner rejects this combination instead of reporting results from a changed theorem environment. A valid solve-rate run requires a compatible backport of the current search and its `jev_benchmark?` adapter to the benchmark pin.

## Tests

```bash
python3 -m unittest tests.test_benchmark4 -v
```

The tests need no dataset, network, or credential. They verify the committed pilot digest, proof-field exclusion, prefix-only materialization, target and suffix leakage checks, credential stripping, exact pin rejection, structured marker parsing, and process-group timeout enforcement.
