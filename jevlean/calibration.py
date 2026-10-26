"""Run the frozen bounded-search calibration fixture without network access."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .experiment import ROOT, _lean_path

FIXTURE = ROOT / "Calibration.lean"
DEFAULT_OUTPUT = ROOT / "artifacts" / "search-calibration-metrics.json"
FIELDS = (
    "fixture", "mode", "solved", "replay_success", "jev_calls", "expanded_nodes",
    "attempted_transitions", "admitted_successors", "duplicate_successors",
    "transposition_entries", "wall_ms",
)
LINE = re.compile(r"JEV_CALIBRATION::(.+)")
# Frozen solve and replay outcomes; changes require an explicit expectation update.
EXPECTED_BASELINE = {
    "local_baseline": True,
    "retrieval": True,
    "rewrite": False,
    "unfolding": True,
    "local_application": True,
    "structural": False,
}


def run(timeout: float = 180.0) -> list[dict[str, Any]]:
    environment = os.environ.copy()
    environment["LEAN_PATH"] = _lean_path()
    result = subprocess.run(
        ["lean", str(FIXTURE)], cwd=ROOT, env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False,
    )
    if result.returncode:
        raise RuntimeError("calibration fixture failed:\n" + result.stdout[-4000:])
    rows = []
    for match in LINE.finditer(result.stdout):
        values = match.group(1).split("|")
        if len(values) != len(FIELDS):
            raise RuntimeError(f"malformed calibration row: {match.group(0)}")
        row: dict[str, Any] = dict(zip(FIELDS, values))
        for field in ("solved", "replay_success"):
            row[field] = row[field] == "true"
        for field in FIELDS[4:]:
            row[field] = int(row[field])
        rows.append(row)
    if len(rows) != 12:
        raise RuntimeError(f"expected 12 calibration rows, found {len(rows)}")
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    modes = {}
    for mode in ("pre_work", "accumulated"):
        selected = [row for row in rows if row["mode"] == mode]
        modes[mode] = {
            "fixtures": len(selected),
            "solved": sum(row["solved"] for row in selected),
            "replay_success": sum(row["replay_success"] for row in selected),
            "jev_calls": sum(row["jev_calls"] for row in selected),
            "expanded_nodes": sum(row["expanded_nodes"] for row in selected),
            "attempted_transitions": sum(row["attempted_transitions"] for row in selected),
            "admitted_successors": sum(row["admitted_successors"] for row in selected),
            "duplicate_successors": sum(row["duplicate_successors"] for row in selected),
            "wall_ms": sum(row["wall_ms"] for row in selected),
        }
    return {
        "schema_version": 1,
        "fixture": str(FIXTURE.relative_to(ROOT)),
        "ranker": "deterministic identity seam; no network calls",
        "limits": {"pre_work": "new feature bounds disabled", "accumulated": "Config defaults"},
        "modes": modes,
        "rows": rows,
    }


def check(summary: dict[str, Any]) -> None:
    expected = {
        (fixture, mode): baseline if mode == "pre_work" else True
        for fixture, baseline in EXPECTED_BASELINE.items()
        for mode in ("pre_work", "accumulated")
    }
    seen = set()
    for row in summary["rows"]:
        key = (row["fixture"], row["mode"])
        if key not in expected or key in seen:
            raise RuntimeError(f"fixture set changed: unexpected or duplicate {key}")
        seen.add(key)
        for field in ("solved", "replay_success"):
            if row[field] != expected[key]:
                raise RuntimeError(
                    f"calibration outcome changed: {key} {field} "
                    f"expected {expected[key]}, got {row[field]}"
                )
    if seen != expected.keys():
        raise RuntimeError("fixture set changed: missing calibration rows")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify fixture invariants without writing an artifact")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    summary = summarize(run(args.timeout))
    check(summary)
    if not args.check:
        DEFAULT_OUTPUT.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {DEFAULT_OUTPUT.relative_to(ROOT)}")
    print(json.dumps(summary["modes"], sort_keys=True))


if __name__ == "__main__":
    main()
