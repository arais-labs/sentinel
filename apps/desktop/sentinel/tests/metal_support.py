"""Hardware prerequisite checks, independent of the driver under test."""

import ctypes
import sys
import unittest


def require_metal_device(*, metal4=False):
    if sys.platform != "darwin":
        raise unittest.SkipTest("Requires macOS Metal")
    metal = ctypes.CDLL("/System/Library/Frameworks/Metal.framework/Metal")
    metal.MTLCopyAllDevices.argtypes = []
    metal.MTLCopyAllDevices.restype = ctypes.c_void_p
    devices = metal.MTLCopyAllDevices()
    objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    count = ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p)(
        ("objc_msgSend", objc)
    )
    item = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(
        ("objc_msgSend", objc)
    )
    supports = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(
        ("objc_msgSend", objc)
    )
    objc.objc_release.argtypes = [ctypes.c_void_p]
    objc.objc_release.restype = None
    try:
        for index in range(count(devices, objc.sel_registerName(b"count"))):
            device = item(devices, objc.sel_registerName(b"objectAtIndex:"), index)
            # Kosmickrisp's mtl_device_create requires MTLGPUFamilyMetal4 (5002).
            if not metal4 or supports(device, objc.sel_registerName(b"supportsFamily:"), 5002):
                return
    finally:
        objc.objc_release(devices)
    requirement = "Metal 4" if metal4 else "Metal"
    raise unittest.SkipTest(f"No {requirement} GPU device available on this host")
