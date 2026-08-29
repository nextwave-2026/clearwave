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

required = ("install", "lint", "test", "build", "licences", "ci")
for target in required:
    result = subprocess.run(
        ["make", "--always-make", "--dry-run", "--no-print-directory", target],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output = result.stdout.strip()
    commands = [line for line in output.splitlines() if not line.startswith("make: ")]
    if result.returncode or not commands:
        detail = f" ({output})" if output else ""
        fail(
            f"Makefile: required target '{target}' is not reachable from Makefile and included fragments. "
            f"Restore the '{target}:' target because CI calls it by name.{detail}"
        )
PY
