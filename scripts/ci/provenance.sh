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
    fail("README.md: cannot locate the repository. Run this guard from the repository and restore the declaration.")

path = root / "README.md"
try:
    text = path.read_text(encoding="utf-8")
except OSError as error:
    fail(f"README.md: cannot read the provenance declaration ({error}). Restore it before rerunning CI.")

issues = []
if not any(
    re.fullmatch(r"#{1,6}[ \t]+Pre-existing components", line.strip(), re.IGNORECASE)
    for line in text.splitlines()
):
    issues.append("README.md is missing the 'Pre-existing components' section heading")
if "nextwave-2026/nextwave-kit" not in text:
    issues.append("README.md no longer references nextwave-2026/nextwave-kit")

try:
    remotes = subprocess.check_output(
        ["git", "remote"], text=True, stderr=subprocess.DEVNULL
    ).splitlines()
except (OSError, subprocess.CalledProcessError) as error:
    fail(f"git remotes: cannot inspect repository remotes ({error}). Remove any nextwave-kit remote and rerun CI.")

for remote in remotes:
    if remote.lower() == "nextwave-kit":
        issues.append("git remote 'nextwave-kit' exists")
        continue
    try:
        urls = subprocess.check_output(
            ["git", "config", "--get-all", f"remote.{remote}.url"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).splitlines()
    except (OSError, subprocess.CalledProcessError):
        urls = []
    if any("nextwave-kit" in url.lower() for url in urls):
        issues.append(f"git remote '{remote}' points at nextwave-kit")

if issues:
    print(
        "Provenance guard failed: the pre-existing IP declaration protects work authored before "
        "the event and must never be removed or weakened.",
        file=sys.stderr,
    )
    for issue in issues:
        print(f"{issue}. Restore the declaration or remove that remote before rerunning CI.", file=sys.stderr)
    raise SystemExit(1)
PY
