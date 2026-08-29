#!/bin/sh
exec python3 - "$@" <<'PY'
from pathlib import Path
import re
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
    fail("INTERFACES.md: cannot locate the repository. Run this guard from the repository and restore the file.")

path = root / "INTERFACES.md"
try:
    text = path.read_text(encoding="utf-8")
except OSError as error:
    fail(f"INTERFACES.md: cannot read the contract file ({error}). Restore it before rerunning CI.")

headings = list(re.finditer(
    r"^- \*\*Boundary name:\*\* (C[1-6])\b.*$", text, re.MULTILINE
))
expected = {f"C{number}" for number in range(1, 7)}
found = {match.group(1) for match in headings}
errors = []
for contract in sorted(expected - found):
    errors.append(
        f"INTERFACES.md: {contract} is missing. Restore its boundary entry and owner before rerunning CI."
    )

for index, match in enumerate(headings):
    contract = match.group(1)
    if contract not in expected:
        continue
    end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
    section = text[match.start():end]
    owner = re.search(r"^- \*\*Owner:\*\*[ \t]*(.*?)[ \t]*$", section, re.MULTILINE)
    if owner is None or not owner.group(1).strip():
        errors.append(
            f"INTERFACES.md: {contract} has no non-empty **Owner:** line. Restore its owner before rerunning CI."
        )

if errors:
    for error in errors:
        print(error, file=sys.stderr)
    raise SystemExit(1)
PY
