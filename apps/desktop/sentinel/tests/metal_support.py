"""Hardware prerequisite checks, independent of the driver under test."""

import ctypes
import sys
import unittest


def require_metal_device():
    if sys.platform != "darwin":
        raise unittest.SkipTest("Requires macOS Metal")
    metal = ctypes.CDLL("/System/Library/Frameworks/Metal.framework/Metal")
    metal.MTLCreateSystemDefaultDevice.argtypes = []
    metal.MTLCreateSystemDefaultDevice.restype = ctypes.c_void_p
    device = metal.MTLCreateSystemDefaultDevice()
    if not device:
        raise unittest.SkipTest("No Metal GPU device available on this host")
    objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    objc.objc_release.argtypes = [ctypes.c_void_p]
    objc.objc_release.restype = None
    objc.objc_release(device)
