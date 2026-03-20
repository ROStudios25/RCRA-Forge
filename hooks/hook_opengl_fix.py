"""
hooks/hook_opengl_fix.py
Runtime hook applied by PyInstaller at EXE startup.

Fixes PyOpenGL platform detection when running as a frozen Windows executable.
Without this, PyOpenGL may fail to locate opengl32.dll on some systems.
"""

import os
import sys


def _fix_opengl():
    """
    On frozen Windows builds, ensure PyOpenGL can find the platform DLL.
    This must run BEFORE any OpenGL import.
    """
    if not getattr(sys, 'frozen', False):
        return

    # Force PyOpenGL to use the WGL (Windows OpenGL) platform
    os.environ.setdefault('PYOPENGL_PLATFORM', 'win32')

    # Ensure sys._MEIPASS (PyInstaller temp dir) is in DLL search path
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        # Add to PATH so ctypes can find bundled DLLs
        os.environ['PATH'] = meipass + os.pathsep + os.environ.get('PATH', '')
        # Python 3.8+ DLL loading
        try:
            os.add_dll_directory(meipass)
        except (AttributeError, OSError):
            pass


_fix_opengl()
