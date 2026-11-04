"""Per-module versioning: know exactly which part of the system changed, and bump only that part.

How it works
------------
``module_versions.json`` lists every module, the paths it owns, its semantic version and a
SHA-256 *content fingerprint* of those paths. The fingerprint is recomputed from the files, so
an edit, a deleted file or a new file in a module changes it.

====================  ========================================================================
Command               What it does
====================  ========================================================================
``status``            Show every module: version, and whether its files changed since the bump.
``check``             Exit 1 if any module changed without a version bump (used by CI).
``add MODULE``        Register a new module listed in ``MODULES`` at version 0.1.0 (needs ``-m``).
``bump MODULE KIND``  Raise one module's version (patch|minor|major), store the new fingerprint
                      and write a line to CHANGELOG.md. Needs ``-m "what changed"``.
``init``              Create the manifest from ``MODULES`` (first run only).
====================  ========================================================================

Rule: change code -> run ``python tools/module_manifest.py bump <module> <kind> -m "..."``.
Only the touched module's version moves; untouched modules keep theirs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parents[1]
MANIFEST: Final = ROOT / "module_versions.json"
CHANGELOG: Final = ROOT / "CHANGELOG.md"
LOG_MARKER: Final = "<!-- module-log -->"
SEMVER: Final = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# module name -> paths (files or folders, relative to the repository root)
MODULES: Final[dict[str, tuple[str, ...]]] = {
    "core": ("src/reggraph/core",),
    "jurisdictions": ("src/reggraph/jurisdictions",),
    "currency": ("src/reggraph/currency",),
    "i18n": ("src/reggraph/i18n",),
    "security": ("src/reggraph/security",),
    "storage": ("src/reggraph/storage",),
    "regulation": ("src/reggraph/regulation",),
    "fx": ("src/reggraph/fx",),
    "registry": ("src/reggraph/registry",),
    "exposure": ("src/reggraph/exposure",),
    "insurance": ("src/reggraph/insurance",),
    "connectivity": ("src/reggraph/connectivity",),
    "stress": ("src/reggraph/stress",),
    "netio": ("src/reggraph/netio",),
    "connectors": ("src/reggraph/connectors",),
    "dashboard": ("src/reggraph/dashboard",),
    "api": ("src/reggraph/api",),
    "app-shell": (
        "src/reggraph/__init__.py",
        "src/reggraph/config.py",
        "src/reggraph/cli.py",
        "src/reggraph/demo.py",
        "src/reggraph/demo_data.py",
    ),
}
_SKIP_PARTS: Final = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def _files(paths: tuple[str, ...]) -> list[Path]:
    found: list[Path] = []
    for rel in paths:
        target = ROOT / rel
        if target.is_file():
            found.append(target)
        elif target.is_dir():
            found.extend(
                p
                for p in target.rglob("*")
                if p.is_file()
                and not (set(p.relative_to(ROOT).parts) & _SKIP_PARTS)
                and p.suffix != ".pyc"
            )
    return sorted(found, key=lambda p: p.relative_to(ROOT).as_posix())


# Tools that hand over files (editors, AI assistants, upload pipelines) may embed provenance
# data in an SVG: a <metadata> block and a c2pa namespace attribute. It does not change what the
# image shows, so it must not change a module's fingerprint either.
_SVG_METADATA: Final = re.compile(rb"<metadata\b.*?</metadata>", re.DOTALL)
_SVG_C2PA_NS: Final = re.compile(rb'\s+xmlns:c2pa="[^"]*"')


def _normalised(file: Path) -> bytes:
    """File bytes with Windows line endings and SVG provenance metadata removed."""
    data = file.read_bytes().replace(b"\r\n", b"\n")
    if file.suffix == ".svg":
        data = _SVG_C2PA_NS.sub(b"", _SVG_METADATA.sub(b"", data))
    return data


def fingerprint(paths: tuple[str, ...]) -> str:
    """Hash of file names + contents (line endings and SVG provenance metadata normalised)."""
    digest = hashlib.sha256()
    for file in _files(paths):
        digest.update(file.relative_to(ROOT).as_posix().encode("utf-8") + b"\0")
        digest.update(_normalised(file) + b"\0")
    return digest.hexdigest()


def load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return data


def save(data: dict[str, Any]) -> None:
    MANIFEST.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def init() -> int:
    if MANIFEST.exists():
        sys.stderr.write("manifest already exists; use bump\n")
        return 1
    modules = {
        name: {"version": "0.1.0", "paths": list(paths), "fingerprint": fingerprint(paths)}
        for name, paths in MODULES.items()
    }
    save({"schema": 1, "modules": modules})
    return 0


def changed_modules(data: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    for name, info in data["modules"].items():
        if fingerprint(tuple(info["paths"])) != info["fingerprint"]:
            changed.append(name)
    return changed


def uncovered_files() -> list[str]:
    """Source files that no module owns (they would escape versioning)."""
    owned = {f for paths in MODULES.values() for f in _files(paths)}
    every = {
        p
        for p in (ROOT / "src").rglob("*")
        if p.is_file()
        and p.suffix != ".pyc"
        and not (set(p.relative_to(ROOT).parts) & _SKIP_PARTS)
        and "egg-info" not in p.as_posix()
    }
    return sorted(p.relative_to(ROOT).as_posix() for p in every - owned)


def status() -> int:
    data = load()
    changed = set(changed_modules(data))
    for name, info in sorted(data["modules"].items()):
        flag = "CHANGED (needs bump)" if name in changed else "ok"
        sys.stdout.write(f"{name:<14} {info['version']:<8} {flag}\n")
    return 0


def check() -> int:
    data = load()
    problems = [f"module '{m}' changed without a version bump" for m in changed_modules(data)]
    problems += [f"file '{f}' belongs to no module" for f in uncovered_files()]
    declared = set(data["modules"])
    problems += [f"module '{m}' missing from manifest" for m in sorted(set(MODULES) - declared)]
    for problem in problems:
        sys.stderr.write(problem + "\n")
    if problems:
        sys.stderr.write("fix: python tools/module_manifest.py bump <module> <kind> -m '...'\n")
        return 1
    sys.stdout.write("all modules consistent\n")
    return 0


def _log(module: str, version: str, kind: str, message: str) -> int:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    line = f"- **{module} {version}** ({stamp}, {kind}): {message.strip()}"
    text = CHANGELOG.read_text(encoding="utf-8")
    if LOG_MARKER not in text:
        sys.stderr.write("CHANGELOG.md has no module-log marker\n")
        return 2
    CHANGELOG.write_text(text.replace(LOG_MARKER, LOG_MARKER + "\n" + line, 1), encoding="utf-8")
    return 0


def add(module: str, message: str) -> int:
    data = load()
    if module in data["modules"]:
        sys.stderr.write(f"module '{module}' is already registered; use bump\n")
        return 2
    if module not in MODULES or not message.strip():
        sys.stderr.write("module must be listed in MODULES and a message (-m) is required\n")
        return 2
    paths = MODULES[module]
    data["modules"][module] = {
        "version": "0.1.0",
        "paths": list(paths),
        "fingerprint": fingerprint(paths),
    }
    save(data)
    sys.stdout.write(f"{module} added at 0.1.0\n")
    return _log(module, "0.1.0", "new", message)


def _next(version: str, kind: str) -> str:
    match = SEMVER.match(version)
    if not match:
        raise ValueError(f"bad version {version!r}")
    major, minor, patch = (int(x) for x in match.groups())
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def bump(module: str, kind: str, message: str) -> int:
    data = load()
    if module not in data["modules"]:
        sys.stderr.write(f"unknown module '{module}'\n")
        return 2
    if not message.strip():
        sys.stderr.write("a change message is required (-m)\n")
        return 2
    info = data["modules"][module]
    info["version"] = _next(info["version"], kind)
    info["fingerprint"] = fingerprint(tuple(info["paths"]))
    save(data)
    if _log(module, info["version"], kind, message) != 0:
        return 2
    sys.stdout.write(f"{module} -> {info['version']}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="module_manifest")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    sub.add_parser("check")
    a = sub.add_parser("add")
    a.add_argument("module")
    a.add_argument("-m", "--message", required=True)
    b = sub.add_parser("bump")
    b.add_argument("module")
    b.add_argument("kind", choices=["patch", "minor", "major"])
    b.add_argument("-m", "--message", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "init":
        return init()
    if args.cmd == "status":
        return status()
    if args.cmd == "check":
        return check()
    if args.cmd == "add":
        return add(args.module, args.message)
    return bump(args.module, args.kind, args.message)


if __name__ == "__main__":
    raise SystemExit(main())
