"""
Imports the modules it is given in that order, then checks that every copy of the Visual C++ runtime in this
process comes from the folder of the install's python.exe. Runs on Windows only.
"""

import ctypes
import importlib
import os
import sys
from ctypes import wintypes

for module in sys.argv[1:]:
    importlib.import_module(module)

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.K32EnumProcessModules.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.HMODULE),
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]

handles = (wintypes.HMODULE * 4096)()
needed = wintypes.DWORD()
if not kernel32.K32EnumProcessModules(
    kernel32.GetCurrentProcess(), handles, ctypes.sizeof(handles), ctypes.byref(needed)
):
    raise ctypes.WinError(ctypes.get_last_error())

paths = []
for handle in handles[: needed.value // ctypes.sizeof(wintypes.HMODULE)]:
    buffer = ctypes.create_unicode_buffer(32768)
    kernel32.GetModuleFileNameW(handle, buffer, len(buffer))
    paths.append(buffer.value)

base = os.path.normcase(sys.base_prefix)
copies = [path for path in paths if os.path.basename(path).lower() == 'msvcp140.dll']
print(*copies, sep='\n')
if not copies:
    sys.exit('msvcp140.dll is not loaded')
if any(os.path.dirname(os.path.normcase(path)) != base for path in copies):
    sys.exit(f'msvcp140.dll should only come from {sys.base_prefix}')
