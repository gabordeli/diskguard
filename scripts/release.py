#!/usr/bin/env python3
"""Bump the plugin version and move the Unreleased notes into a release entry."""

import argparse
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "diskguard" / "version.py"
CHANGELOG = ROOT / "CHANGELOG.md"

VERSION_RE = re.compile(
    r'^__version__ = "(\d+)\.(\d+)\.(\d+)"$', re.MULTILINE
)


def read_version() -> str:
    match = VERSION_RE.search(VERSION_FILE.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit(f"cannot read version from {VERSION_FILE}")
    return ".".join(match.groups())


def bump(current: str, kind: str) -> str:
    major, minor, patch = (int(part) for part in current.split("."))
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise SystemExit("release type must be patch, minor or major")


UNRELEASED_RE = re.compile(
    r"^## \[Unreleased\]\s*\n(.*?)(?=^## \[|\Z)",
    re.MULTILINE | re.DOTALL,
)


def update_changelog(version: str) -> None:
    today = date.today().isoformat()
    fallback = f"- Release {version}."
    content = (
        CHANGELOG.read_text(encoding="utf-8")
        if CHANGELOG.exists()
        else "# Changelog\n\n"
    )
    match = UNRELEASED_RE.search(content)
    if match:
        body = match.group(1).strip() or fallback
        entry = f"## [{version}] - {today}\n\n{body}\n"
        content = (
            content[: match.start()]
            + f"## [Unreleased]\n\n{entry}\n"
            + content[match.end() :]
        )
    else:
        entry = f"## [{version}] - {today}\n\n{fallback}\n"
        first = re.search(r"^## \[", content, re.MULTILINE)
        unreleased = f"## [Unreleased]\n\n{entry}\n"
        if first:
            content = content[: first.start()] + unreleased + content[first.start() :]
        else:
            content = content.rstrip() + "\n\n" + unreleased
    CHANGELOG.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["patch", "minor", "major"])
    parser.add_argument("--version", default="", help="explicit x.y.z override")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    current = read_version()
    version = args.version or bump(current, args.kind)
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit("--version must be x.y.z")

    if not args.dry_run:
        VERSION_FILE.write_text(
            f'__version__ = "{version}"\n', encoding="utf-8"
        )
        update_changelog(version)
    print(version)


if __name__ == "__main__":
    sys.exit(main())
