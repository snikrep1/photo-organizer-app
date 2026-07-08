# App icons

Source-of-truth icon assets used when packaging Photo Organizer.

- `app.ico`  — Windows executable icon (multi-size: 16/32/48/256).
- `app.icns` — macOS `.app` bundle icon.
- `app.png`  — 512x512 master PNG; runtime fallback icon.
- `png/icon-<size>.png` — multi-resolution PNGs for a crisp runtime `QIcon`
  and for Linux hicolor theme installation.

Wiring:
- `photo_organizer.spec` sets `icon=` for the Windows EXE and macOS BUNDLE and
  ships this folder as bundled data.
- `photo_organizer.py` loads these at runtime via `app.setWindowIcon(...)`
  (this is what gives Linux its window/taskbar icon).
- `packaging/linux/install-icons.sh` installs them into the hicolor theme.
