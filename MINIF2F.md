# miniF2F Test pilot

Use this pilot for a fixed 75-target, high-contamination external comparison.

`data/minif2f-test-4.30-pilot.json` vendors only the ported formal statements. It fixes the Google DeepMind miniF2F `Test` split at commit `f0a20e14c1eeccd859d51bb4c2b3ee487889c303`, the upstream source hashes, Lean `v4.30.0`, Mathlib `c5ea00351c28e24afc9f0f84379aa41082b1188f`, 75 accepted targets, and all 169 excluded targets. The target order is SHA-256 of its canonical theorem name. Every Test statement built as a standalone declaration with `import Mathlib`; the first 75 are accepted.

The manifest has no miniF2F docstrings or proof bodies. `jevlean.minif2f::materialize_target` creates one standalone declaration per worker. It imports Mathlib and, for search only, `JevLean`. No miniF2F module, target declaration, sibling target, or later declaration enters Lean's environment. The allowed-premise scope is Mathlib declarations only. The runner mounts no upstream miniF2F checkout or manifest in its worker workspace. Search and fresh replay run in separate credential-free bubblewrap processes. Replay imports no `JevLean`.

Audit the vendored source against the pinned upstream when needed:

```bash
python3 -m jevlean.minif2f acquire --output /path/to/upstream-audit
python3 -m jevlean.minif2f freeze \
  --upstream-root /path/to/upstream-audit/miniF2F \
  --output /path/to/checked-manifest.json
```

`freeze` recompiles all 244 standalone statements before it writes a manifest.

Run the pilot against this project's pin:

```bash
python3 -m jevlean.minif2f audit
python3 -m jevlean.minif2f run --output /path/to/results --timeout 30
```

`run` resumes matching task artifacts and writes `summary.json`. It records the frozen target digest, process output, generated proof, and fresh-process replay result. Do not report this pilot as evidence against model contamination.

Run its offline checks with:

```bash
python3 -m unittest tests.test_minif2f -v
```
