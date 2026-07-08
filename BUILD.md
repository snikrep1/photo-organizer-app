# Building Photo Organizer

Produces a single standalone executable — end users don't need Python installed.

## Local build (current platform only)

Install the dependencies once, then run the build script:

```bash
# Linux / macOS
python3 -m venv .venv
.venv/bin/pip install pyinstaller PyQt6 Pillow
.venv/bin/python build.py

# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\pip install pyinstaller PyQt6 Pillow
.venv\Scripts\python build.py
```

Output lands in `dist/`:

| Platform | Artifact |
|----------|----------|
| Linux    | `dist/photo-organizer` (single file, ~60 MB) |
| Windows  | `dist/PhotoOrganizer.exe` |
| macOS    | `dist/PhotoOrganizer.app` (drag to `/Applications`) |

## All three platforms at once

PyInstaller doesn't cross-compile. Use the GitHub Actions workflow in
`.github/workflows/build.yml` — push a tag like `v1.0.0` and it builds
Linux, Windows, and macOS in parallel and attaches them to a GitHub release.
You can also trigger it manually from the Actions tab (`workflow_dispatch`).

## Notes on the NAS mount feature

The "Mount NAS" button only appears on Linux, because it shells out to
`mount.cifs` (which requires `sudo` and the `cifs-utils` package). On
Windows and macOS, mount the SMB share via the OS (Finder → Go → Connect
to Server, or `\\server\share` in Explorer), then use **Browse…** to point
the app at the mounted location.

## Application icons

Icon assets live in `resources/icons/`:

| File | Used for |
|------|----------|
| `app.ico` | Windows `.exe` icon (embedded by PyInstaller) |
| `app.icns` | macOS `.app` bundle icon |
| `app.png` | 512×512 master; runtime fallback icon |
| `png/icon-<size>.png` | multi-res runtime `QIcon` + Linux hicolor install |

How each OS gets its icon:

- **Windows** — `photo_organizer.spec` passes `icon=app.ico` to `EXE`, so the
  icon is embedded in `PhotoOrganizer.exe`. At runtime the app also sets an
  explicit AppUserModelID so the taskbar shows its own icon.
- **macOS** — the spec passes `icon=app.icns` to `BUNDLE`, giving
  `PhotoOrganizer.app` its Finder/Dock icon.
- **Linux** — executables can't embed an icon, so the app sets the window/dock
  icon at runtime via `app.setWindowIcon(...)`. For menu/launcher integration,
  run `packaging/linux/install-icons.sh` to install the hicolor icons and the
  `.desktop` entry.

The PNGs are bundled into every build (`datas` in the spec), so the runtime
window icon works on all three platforms.
