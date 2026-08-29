#!/bin/sh
exec python3 - "$@" <<'PY'
from pathlib import Path
import json
import os
import re
import subprocess
import sys

FILES = ("STATUS.md", "DECISIONS.md")
DATED_ENTRY = re.compile(
    r"^- 20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}"
    r"(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})(?:\s|$)"
)
ZERO_SHA = "0" * 40


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def git_root() -> Path:
    try:
        return Path(subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], text=True, stderr=subprocess.DEVNULL
        ).strip())
    except (OSError, subprocess.CalledProcessError):
        fail("STATUS.md and DECISIONS.md: cannot locate the repository. Run this guard from the repository.")


def event_payload() -> dict:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return {}
    try:
        return json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def base_ref() -> str | None:
    if len(sys.argv) > 1 and sys.argv[1]:
        return sys.argv[1]
    if os.environ.get("BASE_SHA"):
        return os.environ["BASE_SHA"]

    payload = event_payload()
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event == "pull_request":
        return os.environ.get("GITHUB_BASE_SHA") or payload.get("pull_request", {}).get("base", {}).get("sha")
    if event == "push":
        before = payload.get("before") or os.environ.get("GITHUB_BEFORE")
        if before and before != ZERO_SHA:
            return before
        sha = os.environ.get("GITHUB_SHA", "HEAD")
        try:
            return subprocess.check_output(
                ["git", "rev-parse", f"{sha}^"], text=True, stderr=subprocess.DEVNULL
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    return "HEAD"


root = git_root()
os.chdir(root)
base = base_ref()
if not base:
    print("append-only check skipped: no base commit is available; this is allowed for the first commit.")
    raise SystemExit(0)
try:
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{base}^{{commit}}"],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
except (OSError, subprocess.CalledProcessError):
    print(f"append-only check skipped: base ref {base!r} cannot be determined.")
    raise SystemExit(0)

violations = []
for filename in FILES:
    try:
        diff = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--unified=0", "--no-color", commit, "--", filename],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        fail(f"{filename}: cannot compare against base commit {commit}. Restore repository history and rerun CI ({error}).")
    for raw_line in diff.splitlines():
        if raw_line.startswith("-") and not raw_line.startswith("---"):
            deleted_line = raw_line[1:]
            if DATED_ENTRY.match(deleted_line):
                violations.append((filename, deleted_line))

if violations:
    for filename, line in violations:
        print(
            f"{filename}: append-only violation; pre-existing dated entry was modified or removed: {line}",
            file=sys.stderr,
        )
        print(
            f"{filename}: restore that exact dated line and add a new entry instead before rerunning CI.",
            file=sys.stderr,
        )
    raise SystemExit(1)
PY
