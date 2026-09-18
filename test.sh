#!/usr/bin/env bash
set -euo pipefail

/opt/bots/bin/lean-cache use .
/opt/bots/bin/lean-cache check-env
/opt/bots/bin/lean-cache build --wait . @pantograph/repl @/JevLean
env -u TYPESAFE_API_KEY lake env lean JevLeanTests.lean
TYPESAFE_API_KEY=test JEV_RANK_ORDER=A2,A1 python3 -m jevlean.rank_broker --port 18765 &
broker_pid=$!
trap 'kill "$broker_pid" 2>/dev/null || true' EXIT
TYPESAFE_API_KEY=test JEV_RANK_BROKER_PORT=18765 lake env lean JevLeanBrokerTests.lean
kill "$broker_pid"
wait "$broker_pid" 2>/dev/null || true
trap - EXIT
PANTOGRAPH_SPIKE_RESULTS=artifacts/pantograph-spike-results.json \
  python3 -m unittest discover -s tests -v
python3 -m jevlean.experiment check-lean
python3 -m jevlean.experiment metrics >/dev/null
python3 -m jevlean.progress check-lean
python3 -m jevlean.progress metrics >/dev/null
