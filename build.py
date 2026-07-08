#!/usr/bin/env python3
"""Build Photo Organizer as a standalone executable for the current platform.

Usage:
    python3 build.py

Produces:
    dist/photo-organizer        (Linux)
    dist/PhotoOrganizer.exe     (Windows)
    dist/PhotoOrganizer.app/    (macOS .app bundle)

Run the same command on each platform — this script only builds the native
binary for the machine it's running on. Cross-compiling to other OSes is not
supported by PyInstaller; use the GitHub Actions workflow for that.
"""

import subprocess
import sys
import shutil
from pathlib import Path
from typing import Optional

HERE = Path(__file__).parent.resolve()


def ensure(cmd: list, cwd: Optional[Path] = None):
    print(f"$ {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=cwd)
    if res.returncode != 0:
        sys.exit(res.returncode)


def main():
    # Verify PyInstaller is importable in this Python
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller not installed. Install it first:")
        print("    python3 -m pip install pyinstaller PyQt6 Pillow")
        sys.exit(1)

    # Clean previous builds
    for d in ("build", "dist"):
        p = HERE / d
        if p.exists():
            print(f"Removing stale {d}/")
            shutil.rmtree(p)

    # Run PyInstaller using the spec
    ensure(
        [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
         "photo_organizer.spec"],
        cwd=HERE,
    )

    out = HERE / "dist"
    if sys.platform == "darwin":
        app_bundle = out / "PhotoOrganizer.app"
        # Re-sign the bundle after assembly. PyInstaller only relocates Qt's
        # own data files into Contents/Resources for code-signing compliance;
        # any extra bundled data (our resources/icons/) can leave the .app's
        # ad-hoc signature invalid. On Apple Silicon that makes the app crash
        # at launch inside CoreFoundation (__CFCheckCFInfoPACSignature) before
        # Python runs. A fresh deep ad-hoc re-sign re-seals everything.
        print("Re-signing app bundle (ad-hoc)…")
        ensure(["codesign", "--force", "--deep", "--sign", "-",
                "--timestamp=none", str(app_bundle)])
        ensure(["codesign", "--verify", "--deep", "--strict",
                "--verbose=2", str(app_bundle)])
        print(f"\n[OK] macOS app bundle built: {app_bundle}")
        print(f"     Drag into /Applications to install.")
    elif sys.platform == "win32":
        print(f"\n[OK] Windows executable built: {out / 'PhotoOrganizer.exe'}")
    else:
        print(f"\n[OK] Linux executable built: {out / 'photo-organizer'}")


if __name__ == "__main__":
    main()
