#!/bin/sh
exec python3 - "$@" <<'PY'
from pathlib import Path
import subprocess
import sys


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


try:
    root = Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True, stderr=subprocess.DEVNULL
    ).strip())
except (OSError, subprocess.CalledProcessError):
    fail("Makefile: cannot locate the repository. Run this guard from the repository and restore the target names.")

path = root / "Makefile"
try:
    lines = path.read_text(encoding="utf-8").splitlines()
except OSError as error:
    fail(f"Makefile: cannot read the target contract ({error}). Restore it before rerunning CI.")

found = set()
for line in lines:
    if not line or line[0].isspace() or ":" not in line:
        continue
    names = line.split(":", 1)[0].split()
    found.update(name for name in names if name and not name.startswith("."))

required = ("install", "lint", "test", "build", "licences", "ci")
missing = [target for target in required if target not in found]
if missing:
    for target in missing:
        print(
            f"Makefile: required target '{target}' is missing. Restore the '{target}:' target because CI calls it by name.",
            file=sys.stderr,
        )
    raise SystemExit(1)
PY
