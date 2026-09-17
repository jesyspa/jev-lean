#!/usr/bin/env bash
set -euo pipefail

lean-cache check-env
lean-cache build --wait .
python3 -m unittest discover -s tests -v
python3 -m jevlean.experiment check-lean
python3 -m jevlean.experiment metrics >/dev/null
