# -*- mode: python ; coding: utf-8 -*-
"""将统一入口及全部显示模板打包为单文件 Windows EXE。"""

template_files = [
    "MSU2_LINUX.py",
    "MSU2-LINUX-1.py",
    "MSU2_LINUX-2.py",
    "MSU2_LINUX-3.py",
]

analysis = Analysis(
    ["msu2_linux_launcher.py"],
    pathex=[],
    binaries=[],
    datas=[(template_file, ".") for template_file in template_files],
    hiddenimports=[
        "numpy",
        "psutil",
        "serial",
        "serial.tools.list_ports",
        "PIL.Image",
        "PIL.ImageDraw",
        "PIL.ImageFont",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="msu2-linux",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
