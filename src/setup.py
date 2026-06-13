"""py2app build script

Build command (must use homebrew Python, **not Anaconda Python**):

    /opt/homebrew/bin/python3.13 setup.py py2app

Why: Anaconda Python's C extensions (_sqlite3 / _ssl / lz4, etc.) depend on
Anaconda-private dylibs (libsqlite3.0, libssl.3, liblz4.1, etc.), which
py2app doesn't bundle by default, so the bundle fails at runtime with
missing symbols. homebrew / python.org Python links against system-level
libsqlite3, libssl, etc., and can be packaged directly into a
distributable .app.
"""
from setuptools import setup

APP = ["ai-limit-app.py"]
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "ai-limit.icns",
    "packages": ["rumps", "browser_cookie3", "Cryptodome"],
    "includes": ["usage", "i18n"],
    "plist": {
        "LSUIElement": True,                          # No Dock icon
        "CFBundleName": "ai-limit",
        "CFBundleDisplayName": "ai-limit",
        "CFBundleIdentifier": "com.nanjianggroup.ai-limit",
        "CFBundleVersion": "0.3.8",
        "CFBundleShortVersionString": "0.3.8",
        "NSHumanReadableCopyright": "© 2026 Nan-Jiang Group",
    },
}

setup(
    name="ai-limit",
    app=APP,
    options={"py2app": OPTIONS},
)
