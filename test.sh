#!/usr/bin/env bash
set -euo pipefail

if command -v lean-cache >/dev/null 2>&1; then
  lean-cache use .
  lean-cache check-env
  lean-cache build --wait . @/JevLean
else
  lake build @/JevLean
fi
env -u TYPESAFE_API_KEY lake env lean JevLeanTests.lean

broker_log=$(mktemp)
TYPESAFE_API_KEY=test JEV_RANK_ORDER=A2,A1 python3 -m jevlean.model_broker --port 0 >"$broker_log" 2>&1 &
broker_pid=$!
trap 'kill "$broker_pid" 2>/dev/null || true; rm -f "$broker_log"' EXIT
for _ in $(seq 1 100); do
  if grep -q 'jev model broker ready' "$broker_log"; then
    broker_port=$(sed -n 's/.*127\.0\.0\.1:\([0-9][0-9]*\).*/\1/p' "$broker_log")
    break
  fi
  if ! kill -0 "$broker_pid" 2>/dev/null; then
    cat "$broker_log" >&2
    exit 1
  fi
  sleep 0.05
done
: "${broker_port:?model broker did not become ready}"
env -u TYPESAFE_API_KEY JEV_MODEL_BROKER_PORT="$broker_port" lake env lean JevLeanBrokerTests.lean
kill "$broker_pid"
wait "$broker_pid" 2>/dev/null || true
trap - EXIT
rm -f "$broker_log"

python3 -m unittest discover -s tests -v
python3 -m jevlean.experiment check-lean
python3 -m jevlean.experiment metrics >/dev/null
python3 -m jevlean.progress check-lean
python3 -m jevlean.progress metrics >/dev/null
python3 -m jevlean.calibration --check

if [[ ${RUN_PANTOGRAPH_SPIKE:-0} == 1 ]]; then
  if command -v lean-cache >/dev/null 2>&1; then
    lean-cache build --wait . @pantograph/repl
  else
    lake build @pantograph/repl
  fi
  PANTOGRAPH_SPIKE_RESULTS="$(mktemp)" python3 -m unittest tests.test_pantograph_spike -v
fi
