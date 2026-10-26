"""Read-only verification of frozen Lean benchmark outcomes."""

import difflib
import json
from pathlib import Path
from typing import Any


def verify_or_regenerate(
    path: Path, actual: dict[str, Any], *, regenerate: bool, command: str
) -> None:
    rendered = json.dumps(actual, indent=2, sort_keys=True) + "\n"
    if regenerate:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        return
    hint = f"For an intentional update, run: {command} --regenerate"
    try:
        expected = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Cannot read frozen outcomes at {path}: {error}\n{hint}") from error
    if expected != actual:
        diff = "".join(difflib.unified_diff(
            (json.dumps(expected, indent=2, sort_keys=True) + "\n").splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=str(path),
            tofile="computed outcomes",
        ))
        raise RuntimeError(f"Frozen Lean outcomes mismatch; oracle left unchanged.\n{diff}\n{hint}")
