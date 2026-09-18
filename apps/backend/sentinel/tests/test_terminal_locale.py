"""The shell prompt needs a UTF-8 ctype so readline measures it in columns."""

from __future__ import annotations

import os
import subprocess

from app.services.runtime.tmux import SENTINEL_BASHRC


def _ctype(tmp_path, variable, **overrides):
    rc = tmp_path / "bashrc"
    rc.write_text(SENTINEL_BASHRC)
    env = {
        key: value for key, value in os.environ.items() if key not in {"LANG", "LC_ALL", "LC_CTYPE"}
    }
    env.update(TERM="xterm-256color", **overrides)
    result = subprocess.run(
        [
            "/bin/bash",
            "--noprofile",
            "--rcfile",
            str(rc),
            "-ic",
            f'printf %s "${{{variable}:-}}"',
        ],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_unconfigured_locale_falls_back_to_utf8(tmp_path):
    assert "utf" in _ctype(tmp_path, "LC_CTYPE").lower()


def test_configured_utf8_locale_is_left_alone(tmp_path):
    assert _ctype(tmp_path, "LC_CTYPE", LANG="en_US.UTF-8") == ""


def test_non_utf8_locale_is_corrected_for_the_prompt(tmp_path):
    assert "utf" in _ctype(tmp_path, "LC_CTYPE", LANG="C").lower()
