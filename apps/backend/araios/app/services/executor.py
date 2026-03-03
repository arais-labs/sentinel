"""Python action executor with permissive sandbox.

Available in action code:
  params   – dict of input params from the caller
  secrets  – dict of module secrets (key → value, resolved from DB)
  record   – current record dict (data/page modules only)
  http     – httpx.AsyncClient instance (use: await http.get(...))
  result   – set this variable to return a custom response

Everything in Python builtins is available. Only subprocess execution
and direct os.system/os.popen calls are blocked.
"""
import asyncio
import builtins
import base64
import datetime
import hashlib
import hmac
import json
import math
import re
import urllib.parse
from typing import Any

import httpx


def _make_safe_import(blocked: set):
    """Return an __import__ that blocks a set of top-level module names."""
    real_import = builtins.__import__

    def _import(name, *args, **kwargs):
        if name.split(".")[0] in blocked:
            raise ImportError(f"Module '{name}' is not available in action code")
        return real_import(name, *args, **kwargs)

    return _import


# Modules that could do real damage on the host
_BLOCKED_MODULES = {"subprocess", "pty", "multiprocessing", "ctypes", "signal"}

# Full builtins dict with dangerous import replaced
_BUILTINS = {**builtins.__dict__, "__import__": _make_safe_import(_BLOCKED_MODULES)}

# Pre-import os but strip exec/system/popen so file I/O still works
import os as _os
_SAFE_OS = type(_os)("os")  # fresh module shell
for _attr in dir(_os):
    if _attr not in ("system", "popen", "execv", "execve", "execvp", "execvpe", "spawnl",
                     "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp",
                     "spawnvpe", "fork", "forkpty"):
        try:
            setattr(_SAFE_OS, _attr, getattr(_os, _attr))
        except AttributeError:
            pass


async def execute_action(code: str, context: dict) -> dict:
    """Execute action code with injected context.

    context keys typically include: params, secrets, record (optional).
    Returns whatever the code sets as `result`, or {"ok": True}.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        sandbox_ns: dict[str, Any] = {
            "__builtins__": _BUILTINS,
            # Stdlib conveniences
            "json": json,
            "re": re,
            "math": math,
            "base64": base64,
            "hashlib": hashlib,
            "hmac": hmac,
            "datetime": datetime,
            "urllib": urllib,
            "os": _SAFE_OS,
            # HTTP client — supports both sync-style (.get/.post) and await
            "http": client,
            # Injected context (params, secrets, record, ...)
            **context,
        }

        loop = asyncio.get_event_loop()
        try:
            # Run in thread so blocking code doesn't stall the event loop
            await loop.run_in_executor(
                None,
                # Use one namespace for globals+locals so closures/comprehensions can
                # reliably resolve names created inside action code.
                lambda: exec(compile(code, "<action>", "exec"), sandbox_ns, sandbox_ns),
            )
        except Exception as e:
            return {"ok": False, "error": str(e)}

    result = sandbox_ns.get("result")
    return result if isinstance(result, dict) else {"ok": True}
