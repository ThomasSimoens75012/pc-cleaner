# -*- mode: python ; coding: utf-8 -*-
"""
opencleaner.spec — Configuration PyInstaller pour OpenCleaner
Usage : pyinstaller opencleaner.spec
"""

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[str(Path('.').resolve())],
    binaries=[],
    datas=[
        ('templates',  'templates'),
        ('static',     'static'),
    ],
    hiddenimports=[
        # Flask / Werkzeug
        'flask',
        'werkzeug',
        'werkzeug.serving',
        'werkzeug.routing',
        'werkzeug.exceptions',
        'jinja2',
        'jinja2.ext',
        'markupsafe',
        'click',
        # PyQt6
        'PyQt6',
        'PyQt6.QtWidgets',
        'PyQt6.QtWebEngineWidgets',
        'PyQt6.QtWebEngineCore',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtNetwork',
        # Stdlib
        'ctypes',
        'winreg',
        'concurrent.futures',
        'queue',
        'threading',
        'uuid',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='OpenCleaner',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,           # pas de fenêtre console
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='assets/icon.ico',  # décommenter quand l'icône sera disponible
    uac_admin=False,         # l'app gère l'élévation elle-même (CCleaner pattern)
    version='version_info.txt',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='OpenCleaner',
)
