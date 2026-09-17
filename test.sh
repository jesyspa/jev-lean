#!/usr/bin/env bash
set -euo pipefail

/opt/bots/bin/lean-cache use .
/opt/bots/bin/lean-cache check-env
/opt/bots/bin/lean-cache build --wait . @pantograph/repl @/JevLean
PANTOGRAPH_SPIKE_RESULTS=artifacts/pantograph-spike-results.json \
  python3 -m unittest discover -s tests -v
python3 -m jevlean.experiment check-lean
python3 -m jevlean.experiment metrics >/dev/null
python3 -m jevlean.progress check-lean
python3 -m jevlean.progress metrics >/dev/null
