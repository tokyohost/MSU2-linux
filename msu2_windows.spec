# -*- mode: python ; coding: utf-8 -*-
"""将统一入口及全部显示模板打包为单文件 Windows EXE。"""

template_files = [
    "msu2_dashboard_classic.py",
    "msu2_dashboard_temperature.py",
    "msu2_dashboard_overview.py",
    "msu2_dashboard_disk_temperature.py",
]

analysis = Analysis(
    ["msu2_launcher.py"],
    pathex=[],
    binaries=[],
    datas=[(template_file, ".") for template_file in template_files]
          + [("assets/msu2-monitor.ico", "assets")],
    hiddenimports=[
        "numpy",
        "psutil",
        "serial",
        "serial.tools.list_ports",
        "PIL.Image",
        "PIL.ImageDraw",
        "PIL.ImageFont",
        "pystray._win32",
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
    icon="assets/msu2-monitor.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    uac_admin=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
