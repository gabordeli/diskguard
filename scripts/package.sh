#!/usr/bin/env bash
# Build the portable DiskGuard egg and its SHA256 checksum into dist/.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
DIST="${DIST:-$ROOT/dist}"

cd "$ROOT"
mkdir -p "$DIST"
rm -rf "$ROOT/build"
rm -f "$DIST"/DiskGuard-*.egg "$DIST"/DiskGuard-*.egg.sha256
"$PYTHON" setup.py -q bdist_egg --dist-dir "$DIST"

EGG="$(ls -t "$DIST"/DiskGuard-*.egg | head -n1)"

"$PYTHON" - "$EGG" <<'PY'
import sys
import zipfile

egg = sys.argv[1]
with zipfile.ZipFile(egg) as archive:
    names = set(archive.namelist())
    required = [
        "EGG-INFO/PKG-INFO",
        "EGG-INFO/entry_points.txt",
        "diskguard/gtkui.py",
        "diskguard/core.py",
    ]
    missing = [name for name in required if name not in names]
    if missing:
        raise SystemExit(f"{egg}: missing {missing}")
    print(f"egg: {egg}", file=sys.stderr)
PY

(
    cd "$(dirname "$EGG")"
    sha256sum "$(basename "$EGG")"
) > "$EGG.sha256"

echo "$EGG"
