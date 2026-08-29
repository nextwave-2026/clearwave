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
    fail("LICENCES.md: cannot locate the repository. Run this guard from the repository and update the inventory.")

path = root / "LICENCES.md"
try:
    before = path.read_bytes()
except OSError as error:
    fail(f"LICENCES.md: cannot read the inventory before generation ({error}). Restore it before rerunning CI.")

result = subprocess.run(
    ["make", "licences"], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
)
if result.returncode:
    print(
        "LICENCES.md: make licences failed. Fix the offline inventory generator or its input, then rerun make licences.",
        file=sys.stderr,
    )
    if result.stdout.strip():
        print(result.stdout.rstrip(), file=sys.stderr)
    raise SystemExit(1)

try:
    after = path.read_bytes()
except OSError as error:
    fail(f"LICENCES.md: make licences removed or failed to write the inventory ({error}). Restore it before rerunning CI.")

if before != after:
    fail(
        "LICENCES.md: make licences changed the inventory. Run make licences locally and commit the generated result before rerunning CI."
    )
PY
