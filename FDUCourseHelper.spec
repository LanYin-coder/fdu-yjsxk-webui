# -*- mode: python ; coding: utf-8 -*-

import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files


is_mac = sys.platform == "darwin"
hiddenimports = ["browser_cookie3", *collect_submodules("browser_cookie3"),
                 "webview.platforms.cocoa" if is_mac else "webview.platforms.winforms"]

a = Analysis(
    ["webui.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("webui/templates", "webui/templates"),
        ("webui/static", "webui/static"),
    ] + collect_data_files("webview", includes=["js/**/*"]),
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FDUCourseHelper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Windows workers need real stdin/stdout pipes for progress and interactive
    # CLI compatibility. Hide the parent console; workers use CREATE_NO_WINDOW.
    console=not is_mac,
    hide_console=None if is_mac else "hide-early",
    argv_emulation=False,
    target_arch="arm64" if is_mac else None,
    icon="build/FDUCourseHelper.icns" if is_mac else "assets/app-icon.ico",
    codesign_identity=None,
    entitlements_file=None,
)

collect = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="FDUCourseHelper",
)

if is_mac:
    app = BUNDLE(
        collect,
        name="FDU选课助手.app",
        icon="build/FDUCourseHelper.icns",
        bundle_identifier="cn.fdu.yjsxk.course-helper",
        info_plist={
            "CFBundleDisplayName": "FDU选课助手",
            "CFBundleName": "FDU选课助手",
            "CFBundleShortVersionString": "1.2.0",
            "CFBundleVersion": "3",
            "LSMinimumSystemVersion": "12.3",
            "NSHighResolutionCapable": True,
        },
    )
