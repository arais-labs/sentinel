from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

import pytest

from app.services.runtime.darwin_seatbelt import (
    build_append_seatbelt_tool_roots_script,
    build_seatbelt_profile,
)
from app.services.runtime.workspace import workspace_paths


def _paths():
    return workspace_paths("sess-abc", root="/srv/sentinel")


def test_tool_roots_are_appended_as_fallback_with_python() -> None:
    script = build_append_seatbelt_tool_roots_script(_paths(), "/srv/sentinel/profile.sb")
    # Tool dirs appended (user's tools win), python resolved, and each root gets
    # both subpath reads and ancestor traversal (for realpath on symlinked tools).
    assert 'export PATH="$PATH:${sentinel_tool_path#:}"' in script
    assert '"${sentinel_tool_path#:}:$PATH"' not in script
    assert "python3" in script
    assert 'subpath "%s"' in script
    assert 'path-ancestors "%s"' in script


@pytest.mark.skipif(
    sys.platform != "darwin"
    or shutil.which("sandbox-exec") is None
    or not os.path.exists("/opt/homebrew/bin/python3"),
    reason="requires macOS sandbox-exec + Homebrew python",
)
def test_homebrew_python_runs_under_profile() -> None:
    # Regression for "realpath: /opt/homebrew/bin/: Operation not permitted".
    paths = workspace_paths("sess-x", root="/tmp/sbtest")
    profile = build_seatbelt_profile(paths)
    # The two rules build_append_seatbelt_tool_roots_script appends for /opt/homebrew.
    profile += '\n(allow file-read* file-test-existence (subpath "/opt/homebrew"))'
    profile += '\n(allow file-read-metadata file-test-existence (path-ancestors "/opt/homebrew"))\n'
    handle = tempfile.NamedTemporaryFile("w", suffix=".sb", delete=False)
    handle.write(profile)
    handle.close()
    try:
        result = subprocess.run(
            [
                "sandbox-exec",
                "-f",
                handle.name,
                "/opt/homebrew/bin/python3",
                "-c",
                "print('PYTHON_OK')",
            ],
            capture_output=True,
            text=True,
            cwd="/tmp",
        )
    finally:
        os.unlink(handle.name)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PYTHON_OK"


def test_profile_maps_workspace_executable() -> None:
    profile = build_seatbelt_profile(_paths())
    # Two blocks: system/tool roots + the session workspace.
    assert profile.count("(allow file-map-executable") == 2
    exec_section = profile.rsplit("(allow file-map-executable", 1)[1]
    assert '(subpath "/srv/sentinel/sess-abc")' in exec_section
