#!/bin/sh
exec python3 - "$@" <<'PY'
from pathlib import Path
from datetime import datetime
import json
import os
import re
import subprocess
import sys

FILES = ("STATUS.md", "DECISIONS.md")
ENTRY_START = re.compile(r"^- 20")
ENTRY = re.compile(
    r"^- (?P<timestamp>20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}"
    r"(?::\d{2}(?:\.\d+)?)?Z)(?:\s+|$)(?P<rest>.*)$"
)
HANDLES = {"derek", "andres", "juank", "raul"}
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
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


def added_entries(diff: str) -> list[tuple[int, str]]:
    current_line = None
    entries = []
    for raw_line in diff.splitlines():
        match = HUNK.match(raw_line)
        if match:
            current_line = int(match.group(1))
            continue
        if current_line is None or raw_line.startswith("+++"):
            continue
        if raw_line.startswith("+"):
            added = raw_line[1:]
            if ENTRY_START.match(added):
                entries.append((current_line, added))
            current_line += 1
        elif raw_line.startswith("-"):
            continue
        else:
            current_line += 1
    return entries


root = git_root()
os.chdir(root)
base = base_ref()
if not base:
    print("attribution check skipped: no base commit is available; this is allowed for the first commit.")
    raise SystemExit(0)
try:
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{base}^{{commit}}"],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
except (OSError, subprocess.CalledProcessError):
    print(f"attribution check skipped: base ref {base!r} cannot be determined.")
    raise SystemExit(0)

errors = []
for filename in FILES:
    try:
        current_lines = (root / filename).read_text(encoding="utf-8").splitlines()
        diff = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--unified=0", "--no-color", commit, "--", filename],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
    except OSError as error:
        fail(f"{filename}: cannot read the coordination log ({error}). Restore it before rerunning CI.")
    except subprocess.CalledProcessError as error:
        fail(f"{filename}: cannot compare against base commit {commit}. Restore repository history and rerun CI ({error}).")

    for line_number, line in added_entries(diff):
        match = ENTRY.match(line)
        if match is None:
            errors.append(
                f"{filename}:{line_number}: new dated entry lacks an ISO 8601 UTC timestamp. "
                "Use '- YYYY-MM-DDTHH:MMZ handle ...'."
            )
            continue
        try:
            datetime.fromisoformat(match.group("timestamp").replace("Z", "+00:00"))
        except ValueError:
            errors.append(
                f"{filename}:{line_number}: new dated entry has an invalid UTC timestamp. "
                "Use a real ISO 8601 timestamp ending in Z."
            )
        handle = match.group("rest").split(None, 1)[0] if match.group("rest").split() else ""
        if handle not in HANDLES:
            errors.append(
                f"{filename}:{line_number}: new entry has unknown or missing handle {handle!r}. "
                "Use derek, andres, juank, or raul immediately after the timestamp."
            )
        if filename == "DECISIONS.md":
            second_line = current_lines[line_number] if line_number < len(current_lines) else ""
            if not second_line.lstrip().startswith("-> other side:"):
                errors.append(
                    f"DECISIONS.md:{line_number + 1}: new entry must have a second line beginning "
                    "'-> other side:'. Add the consequence line before rerunning CI."
                )

if errors:
    for error in errors:
        print(error, file=sys.stderr)
    raise SystemExit(1)
PY
