"""Require the distro OpenGL ABI and the EGL vendor to share one context.

Run inside a disposable graphical workspace. This catches a split between
non-GLVND bundled EGL and distro libOpenGL, before Qt dereferences a NULL
glGetString result. No application-specific renderer overrides are used.
"""

import ctypes as C
import json
import os
from pathlib import Path
import sys

session = json.loads(Path("/run/sentinel-desktop/session.json").read_text())
os.environ.update(session["environment"])
user = session["user"]
os.setgroups(user["groups"])
os.setgid(user["gid"])
os.setuid(user["uid"])
assert os.getuid() != 0

egl = C.CDLL("libEGL.so.1")
# Select the actual distro ABI explicitly; never silently retry a different
# library after the intended public dispatch fails.
abi = sys.argv[1]
assert abi in {"glvnd", "mesa"}, "Specify the distribution's GL dispatch ABI"
public_library = "libOpenGL.so.0" if abi == "glvnd" else "libGL.so.1"
gl = C.CDLL(public_library)


def function(library, name, result, *arguments):
    value = getattr(library, name)
    value.restype = result
    value.argtypes = arguments
    return value


pointer, integer, boolean = C.c_void_p, C.c_int, C.c_uint
get_display = function(egl, "eglGetPlatformDisplay", pointer, C.c_uint, pointer, pointer)
initialize = function(egl, "eglInitialize", boolean, pointer, pointer, pointer)
bind_api = function(egl, "eglBindAPI", boolean, C.c_uint)
choose = function(egl, "eglChooseConfig", boolean, pointer, pointer, pointer, integer, pointer)
create = function(egl, "eglCreateContext", pointer, pointer, pointer, pointer, pointer)
current = function(egl, "eglMakeCurrent", boolean, pointer, pointer, pointer, pointer)
destroy = function(egl, "eglDestroyContext", boolean, pointer, pointer)
terminate = function(egl, "eglTerminate", boolean, pointer)
get_proc = function(egl, "eglGetProcAddress", pointer, C.c_char_p)
get_string = function(gl, "glGetString", C.c_char_p, C.c_uint)

# EGL_MESA_platform_surfaceless uses the same render device without requiring
# an extra compositor window. Context dispatch is independent of presentation.
display = get_display(0x31DD, None, None)
assert display and initialize(display, None, None), "EGL display initialization failed"
context = None
try:
    assert bind_api(0x30A2), "EGL could not bind desktop OpenGL"
    attributes = (integer * 5)(0x3040, 8, 0x3033, 1, 0x3038)
    config, count = pointer(), integer()
    assert choose(display, attributes, C.byref(config), 1, C.byref(count)) and count.value
    context_attributes = (integer * 7)(0x3098, 3, 0x30FB, 3, 0x30FD, 1, 0x3038)
    context = create(display, config, None, context_attributes)
    assert context and current(display, None, None, context), "EGL context creation/binding failed"
    address = get_proc(b"glGetString")
    assert address, "EGL did not expose glGetString"
    vendor_get_string = C.CFUNCTYPE(C.c_char_p, C.c_uint)(address)
    values = {}
    for name, token in (("version", 0x1F02), ("renderer", 0x1F01), ("vendor", 0x1F00)):
        vendor_value, public_value = vendor_get_string(token), get_string(token)
        assert vendor_value, f"EGL vendor returned no {name}"
        assert public_value == vendor_value, (
            f"Split EGL/OpenGL context dispatch for {name}: "
            f"EGL={vendor_value!r}, {public_library}={public_value!r}"
        )
        values[name] = public_value.decode()
    assert "llvmpipe" not in values["renderer"].lower()
    assert "softpipe" not in values["renderer"].lower()
    print(json.dumps({"abi": abi, "public_library": public_library, **values}))
finally:
    current(display, None, None, None)
    if context:
        destroy(display, context)
    terminate(display)
