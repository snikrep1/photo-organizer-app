# PyInstaller spec for Photo Organizer.
# Build with:  pyinstaller photo_organizer.spec
# Produces a single-file executable in dist/ for the current platform.
# App icons live in resources/icons/ (app.ico, app.icns, png/).

import sys
from pathlib import Path

block_cipher = None
HERE = Path(SPECPATH)
ICON_DIR = HERE / 'resources' / 'icons'
WIN_ICON = str(ICON_DIR / 'app.ico')
MAC_ICON = str(ICON_DIR / 'app.icns')

a = Analysis(
    ['photo_organizer.py'],
    pathex=[str(HERE)],
    binaries=[],
    datas=[(str(ICON_DIR), 'resources/icons')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'unittest', 'pydoc', 'doctest',
        'test', 'distutils', 'setuptools',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# macOS wants an .app bundle; Win/Linux get a single-file exe
if sys.platform == 'darwin':
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='PhotoOrganizer',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=True,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe, a.binaries, a.zipfiles, a.datas,
        strip=False, upx=False, name='PhotoOrganizer',
    )
    app = BUNDLE(
        coll,
        name='PhotoOrganizer.app',
        icon=MAC_ICON,
        bundle_identifier='com.guy.photoorganizer',
        info_plist={
            'CFBundleShortVersionString': '1.0.0',
            'CFBundleVersion': '1.0.0',
            'NSHighResolutionCapable': 'True',
            'NSPrincipalClass': 'NSApplication',
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name='PhotoOrganizer' if sys.platform == 'win32' else 'photo-organizer',
        icon=WIN_ICON if sys.platform == 'win32' else None,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,       # windowed (GUI) app — no console window on Windows
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
