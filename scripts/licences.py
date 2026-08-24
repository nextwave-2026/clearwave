#!/usr/bin/env python3
"""Generate an offline dependency and licence inventory."""

from __future__ import annotations

import json
import os
import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "LICENCES.md"
BEGIN = "<!-- BEGIN GENERATED LICENCE INVENTORY -->"
END = "<!-- END GENERATED LICENCE INVENTORY -->"
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__"}


def manifest_paths() -> list[Path]:
    paths: list[Path] = []
    for current, directories, files in os.walk(ROOT):
        directories[:] = sorted(
            directory for directory in directories if directory not in SKIP_DIRS
        )
        for filename in sorted(files):
            if filename in {"package.json", "pyproject.toml", "requirements.txt"}:
                paths.append(Path(current) / filename)
    return sorted(paths)


def package_json_dependencies(path: Path) -> list[tuple[str, str]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [(f"unreadable manifest ({error})", "")]

    dependencies: list[tuple[str, str]] = []
    for section in (
        "dependencies",
        "devDependencies",
        "peerDependencies",
        "optionalDependencies",
    ):
        values = document.get(section, {})
        if isinstance(values, dict):
            dependencies.extend(
                (name, str(specification))
                for name, specification in sorted(values.items())
            )
    return dependencies


def pyproject_dependencies(path: Path) -> list[tuple[str, str]]:
    try:
        import tomllib
    except ImportError:
        return [("pyproject.toml requires Python 3.11 or newer", "")]

    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        return [(f"unreadable manifest ({error})", "")]

    dependencies: list[tuple[str, str]] = []
    project = document.get("project", {})
    if isinstance(project, dict):
        values = project.get("dependencies", [])
        if isinstance(values, list):
            dependencies.extend((dependency_name(value), str(value)) for value in values)
        optional = project.get("optional-dependencies", {})
        if isinstance(optional, dict):
            for group, values in sorted(optional.items()):
                if isinstance(values, list):
                    dependencies.extend(
                        (f"{dependency_name(value)} [{group}]", str(value))
                        for value in values
                    )

    poetry = document.get("tool", {}).get("poetry", {})
    if isinstance(poetry, dict):
        dependencies.extend(poetry_table_dependencies(poetry.get("dependencies")))
        groups = poetry.get("group", {})
        if isinstance(groups, dict):
            for group, details in sorted(groups.items()):
                if isinstance(details, dict):
                    for name, specification in poetry_table_dependencies(
                        details.get("dependencies")
                    ):
                        dependencies.append((f"{name} [{group}]", specification))

    build_system = document.get("build-system", {})
    if isinstance(build_system, dict):
        values = build_system.get("requires", [])
        if isinstance(values, list):
            dependencies.extend(
                (f"{dependency_name(value)} [build]", str(value)) for value in values
            )
    return dependencies


def poetry_table_dependencies(value: object) -> list[tuple[str, str]]:
    if not isinstance(value, dict):
        return []
    return [
        (name, specification if isinstance(specification, str) else json.dumps(specification))
        for name, specification in sorted(value.items())
        if name.lower() != "python"
    ]


def requirements_dependencies(path: Path) -> list[tuple[str, str]]:
    dependencies: list[tuple[str, str]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        return [(f"unreadable manifest ({error})", "")]

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r", "--requirement", "-c", "--constraint", "--index-url", "--extra-index-url")):
            dependencies.append((line, "manifest directive"))
            continue
        line = line.split(" #", 1)[0].strip()
        dependencies.append((dependency_name(line), line))
    return dependencies


def dependency_name(value: object) -> str:
    text = str(value).strip()
    match = re.match(r"([A-Za-z0-9][A-Za-z0-9_.-]*)", text)
    return match.group(1) if match else text


def local_node_license(manifest: Path, name: str) -> str:
    directory = manifest.parent
    while True:
        package_path = directory / "node_modules" / Path(*name.split("/")) / "package.json"
        if package_path.is_file():
            try:
                package = json.loads(package_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return "unknown (local package metadata unreadable)"
            license_value = package.get("license")
            if isinstance(license_value, str) and license_value:
                return license_value
            if isinstance(license_value, dict) and license_value.get("type"):
                return str(license_value["type"])
            return "unknown (local package metadata has no licence)"
        if directory == ROOT:
            break
        directory = directory.parent
    return "unknown (not available locally)"


def local_python_license(name: str) -> str:
    try:
        distribution = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return "unknown (not available locally)"

    value = distribution.metadata.get("License", "").strip()
    if value and value.lower() not in {"unknown", "none"}:
        return value
    classifiers = distribution.metadata.get_all("Classifier", [])
    licenses = [
        classifier.removeprefix("License :: ")
        for classifier in classifiers
        if classifier.startswith("License :: ")
    ]
    return ", ".join(licenses) if licenses else "unknown (local metadata has no licence)"


def inventory_for(path: Path) -> tuple[str, list[tuple[str, str, str]]]:
    if path.name == "package.json":
        dependencies = package_json_dependencies(path)
        kind = "Node"
        rows = [
            (name, specification, local_node_license(path, name))
            for name, specification in dependencies
        ]
    elif path.name == "pyproject.toml":
        dependencies = pyproject_dependencies(path)
        kind = "Python"
        rows = [
            (name, specification, local_python_license(dependency_name(name)))
            for name, specification in dependencies
        ]
    else:
        dependencies = requirements_dependencies(path)
        kind = "Python"
        rows = [
            (name, specification, local_python_license(name))
            for name, specification in dependencies
        ]
    return kind, rows


def generated_content(paths: list[Path]) -> str:
    lines = ["## Generated dependency inventory", ""]
    if not paths:
        lines.append("No dependency manifests found.")
        return "\n".join(lines)

    lines.append(
        "Licence values come only from metadata already present on this machine; no network lookup was performed."
    )
    lines.append("")
    for path in paths:
        kind, rows = inventory_for(path)
        relative = path.relative_to(ROOT).as_posix()
        lines.extend([f"### `{relative}` ({kind})", ""])
        if not rows:
            lines.append("No declared dependencies found.")
            lines.append("")
            continue
        lines.extend(["| Dependency | Declaration | Licence |", "| --- | --- | --- |"])
        for name, declaration, licence in rows:
            cells = [name, declaration, licence]
            escaped = [cell.replace("|", "\\|").replace("\n", " ") for cell in cells]
            lines.append("| " + " | ".join(escaped) + " |")
        lines.append("")
    return "\n".join(lines).rstrip()


def update_document(content: str) -> str:
    if BEGIN in content and END in content:
        prefix = content.split(BEGIN, 1)[0].rstrip()
        suffix = content.split(END, 1)[1].lstrip()
        result = f"{prefix}\n\n{BEGIN}\n{generated_content(manifest_paths())}\n{END}"
        if suffix:
            result += f"\n{suffix}"
        return result.rstrip() + "\n"
    return content.rstrip() + f"\n\n{BEGIN}\n{generated_content(manifest_paths())}\n{END}\n"


def main() -> int:
    try:
        content = OUTPUT.read_text(encoding="utf-8")
        OUTPUT.write_text(update_document(content), encoding="utf-8")
    except OSError as error:
        print(f"licence inventory failed: {error}", file=sys.stderr)
        return 1

    paths = manifest_paths()
    if paths:
        print(f"licence inventory updated for {len(paths)} manifest(s)")
    else:
        print("no manifests found: licence inventory is empty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
