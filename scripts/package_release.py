"""Builds the release package: FETCH-<version>.zip and its SHA-256 file.

The package holds the tracked program files plus the built web interface
(frontend/dist with a .prebuilt marker), so installing it needs no Node.js.

    python scripts/package_release.py [--out dist]

Run it from a git checkout after `npm run build` in frontend/.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Not part of an installation.
EXCLUDED_PREFIXES = (".github/", "backend/tests/", "frontend/dist/")
EXCLUDED_FILES = {".gitignore", "backend/requirements-dev.txt"}
TOP_FOLDER = "FETCH"


def read_version() -> str:
    text = (ROOT / "backend" / "fetch_app" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("Could not read __version__ from backend/fetch_app/__init__.py")
    return match.group(1)


def tracked_files() -> list[str]:
    result = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True)
    files = []
    for name in result.stdout.splitlines():
        if name in EXCLUDED_FILES or name.startswith(EXCLUDED_PREFIXES):
            continue
        files.append(name)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="dist", help="output folder (default: dist)")
    args = parser.parse_args()

    version = read_version()
    dist = ROOT / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("frontend/dist/index.html is missing. Run `npm ci && npm run build` in frontend/ first.")
    # Tells install.ps1 and update.ps1 that the interface does not need building.
    (dist / ".prebuilt").write_text(f"{version}\n", encoding="ascii")

    names = tracked_files()
    names += sorted(path.relative_to(ROOT).as_posix() for path in dist.rglob("*") if path.is_file())
    for required in ("install.ps1", "update.ps1", "backend/fetch_app/__init__.py", "backend/requirements.txt", "frontend/dist/index.html"):
        if required not in names:
            raise SystemExit(f"{required} would be missing from the package.")

    out = (ROOT / args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    package = out / f"FETCH-{version}.zip"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in names:
            archive.write(ROOT / name, f"{TOP_FOLDER}/{name}")

    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    (out / f"{package.name}.sha256").write_text(f"{digest}  {package.name}\n", encoding="ascii")
    print(f"{package} ({package.stat().st_size} bytes, {len(names)} files)")
    print(f"sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
