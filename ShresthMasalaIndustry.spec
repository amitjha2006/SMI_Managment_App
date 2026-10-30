# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main_window.py'],
    pathex=[],
    binaries=[],
    datas=[('frontend', 'frontend'), ('VERSION', '.')],
    hiddenimports=['fastapi', 'uvicorn', 'sqlalchemy', 'pydantic', 'pydantic_settings', 'webview', 'openpyxl', 'reportlab', 'PIL', 'jose', 'passlib', 'passlib.handlers.pbkdf2', 'multipart', 'uharfbuzz'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'setuptools', 'pkg_resources', 'scipy', 'pandas', 'matplotlib', 'tkinter'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ShresthMasalaIndustry',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:/Shresth_Masala_Application_New/frontend/assets/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ShresthMasalaIndustry',
)
