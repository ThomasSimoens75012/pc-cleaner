# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — OpenCleaner
# Build : pyinstaller OpenCleaner.spec

import os

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
    ],
    hiddenimports=[
        'win32api', 'win32con', 'win32file', 'win32gui',
        'pywintypes', 'winerror',
        'click.core', 'click.decorators', 'click.exceptions',
        'click.types', 'click.utils', 'click.formatting',
        'click.termui', 'click.parser', 'click.globals',
        'click.testing', 'click.shell_completion',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'test', 'xmlrpc', 'pydoc'],
    noarchive=False,
    optimize=1,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='OpenCleaner',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # Pas de fenêtre console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='static/favicon.ico' if os.path.exists('static/favicon.ico') else None,
)
