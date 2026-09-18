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
    modes = summary["modes"]
    if modes["pre_work"]["fixtures"] != 6 or modes["accumulated"]["fixtures"] != 6:
        raise RuntimeError("fixture set changed")
    if modes["accumulated"]["solved"] < modes["pre_work"]["solved"]:
        raise RuntimeError("accumulated features regressed solve count")
    if modes["accumulated"]["replay_success"] != modes["accumulated"]["solved"]:
        raise RuntimeError("an accumulated closing path failed replay")


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
