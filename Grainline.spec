# -*- mode: python ; coding: utf-8 -*-
"""Build a single Grainline.exe. Run scripts/build.ps1 rather than this file directly."""

from pathlib import Path
import sys

sys.path.insert(0, "src")

from PyInstaller.utils.hooks import collect_all

from grainline.version import __version__

datas, binaries, hiddenimports = collect_all("customtkinter")
datas += [
    ("assets/grainline.ico", "assets"),
    ("assets/grainline.png", "assets"),
]
hiddenimports += ["windnd", "darkdetect"]

parts = [int(piece) if piece.isdigit() else 0 for piece in __version__.split(".")]
while len(parts) < 4:
    parts.append(0)
version = tuple(parts[:4])
version_file = Path("build") / "file_version_info.txt"
version_file.parent.mkdir(exist_ok=True)
version_file.write_text(
    f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version},
    prodvers={version},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
    ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('FileDescription', 'Wood-grain hotend temperatures for G-code'),
          StringStruct('FileVersion', '{__version__}'),
          StringStruct('InternalName', 'Grainline'),
          StringStruct('OriginalFilename', 'Grainline.exe'),
          StringStruct('ProductName', 'Grainline'),
          StringStruct('ProductVersion', '{__version__}')
        ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
    encoding="utf-8",
)

a = Analysis(
    ["src/grainline/__main__.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "numpy", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Grainline",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon="assets/grainline.ico",
    version=str(version_file),
)
