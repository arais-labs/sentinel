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

import ast
import asyncio
import base64
import builtins
import datetime
import hashlib
import hmac
import inspect
import json
import math
import os as _os
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


_BLOCKED_MODULES = {"subprocess", "pty", "multiprocessing", "ctypes", "signal"}
_BUILTINS = {**builtins.__dict__, "__import__": _make_safe_import(_BLOCKED_MODULES)}

# Pre-import os but strip exec/system/popen so file I/O still works
_SAFE_OS = type(_os)("os")
for _attr in dir(_os):
    if _attr not in (
        "system",
        "popen",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "fork",
        "forkpty",
    ):
        try:
            setattr(_SAFE_OS, _attr, getattr(_os, _attr))
        except AttributeError:
            pass


def compile_action(code: str):
    """Compile without executing, using the same syntax for saving and running."""
    return compile(code, "<action>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)


def validate_action_code(actions: list[dict]) -> None:
    for action in actions:
        code = action.get("code")
        if code is None:
            continue
        if not isinstance(code, str):
            raise ValueError(f"Action '{action.get('id')}' code must be a string")
        try:
            compile_action(code)
        except SyntaxError as error:
            raise ValueError(
                f"Action '{action.get('id')}', line {error.lineno}: {error.msg}"
            ) from error


async def execute_action(code: str, context: dict) -> dict:
    """Execute action code with injected context.

    context keys typically include: params, secrets, record (optional).
    Returns whatever the code sets as ``result``, or {"ok": True}.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        sandbox_ns: dict[str, Any] = {
            "__builtins__": _BUILTINS,
            "json": json,
            "re": re,
            "math": math,
            "base64": base64,
            "hashlib": hashlib,
            "hmac": hmac,
            "datetime": datetime,
            "urllib": urllib,
            "os": _SAFE_OS,
            "http": client,
            **context,
        }

        try:
            compiled = compile_action(code)
            if compiled.co_flags & inspect.CO_COROUTINE:
                await eval(compiled, sandbox_ns, sandbox_ns)
            else:
                # Keep blocking synchronous scripts off the application's event loop.
                await asyncio.to_thread(exec, compiled, sandbox_ns, sandbox_ns)
            if "result" not in sandbox_ns:
                return {"ok": True}
            result = sandbox_ns["result"]
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, dict):
                raise TypeError("Action result must be a dictionary")
            return result
        except Exception as error:
            return {"ok": False, "error": str(error)}
