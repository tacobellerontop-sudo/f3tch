"""Builds the single-file FETCH executable with PyInstaller.

    python scripts/build_exe.py [--out dist]

Needs the backend packages, `pip install -r backend/requirements-build.txt`,
and a built web interface (`npm ci && npm run build` in frontend/). The result
is dist/FETCH.exe (dist/FETCH on other systems) plus its SHA-256 file. The
executable contains Python, FETCH and the web interface; it keeps its data in
the folder it is started from.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="dist", help="output folder (default: dist)")
    args = parser.parse_args()

    dist = ROOT / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("frontend/dist/index.html is missing. Run `npm ci && npm run build` in frontend/ first.")
    out = (ROOT / args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    def data(source: Path, target: str) -> str:
        return f"{source}{os.pathsep}{target}"

    with tempfile.TemporaryDirectory() as work:
        command = [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--console",
            "--name", "FETCH",
            "--distpath", str(out), "--workpath", str(Path(work) / "build"), "--specpath", work,
            "--paths", str(BACKEND),
            "--add-data", data(dist, "frontend_dist"),
            "--add-data", data(BACKEND / "alembic", "alembic"),
            "--add-data", data(BACKEND / "alembic.ini", "."),
            # Imported by name at run time, so PyInstaller cannot see them.
            "--collect-submodules", "fetch_app",
            "--collect-submodules", "uvicorn",
            "--collect-submodules", "websockets",
            "--collect-submodules", "alembic",
            "--hidden-import", "sqlalchemy.dialects.sqlite",
            "--hidden-import", "logging.config",
            "--exclude-module", "tkinter",
            "--exclude-module", "pytest",
            str(BACKEND / "fetch_exe.py"),
        ]
        subprocess.run(command, check=True, cwd=ROOT)

    exe = out / ("FETCH.exe" if os.name == "nt" else "FETCH")
    if not exe.is_file():
        raise SystemExit(f"PyInstaller did not produce {exe}")
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    (out / f"{exe.name}.sha256").write_text(f"{digest}  {exe.name}\n", encoding="ascii")
    print(f"{exe} ({exe.stat().st_size} bytes)")
    print(f"sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
