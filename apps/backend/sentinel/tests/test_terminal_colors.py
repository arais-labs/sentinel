from __future__ import annotations

import errno
import os
import pty
import select
import subprocess

import pytest

from app.services.runtime.tmux import SENTINEL_BASHRC


@pytest.mark.parametrize(
    "command",
    [
        "ls -d colored-directory",
        "grep needle colored.txt",
        "git diff --no-index /dev/null colored.txt",
    ],
)
@pytest.mark.parametrize("terminal,no_color", [(True, False), (False, False), (True, True)])
def test_command_colors_follow_output_destination(tmp_path, command, terminal, no_color):
    rc = tmp_path / "bashrc"
    rc.write_text(SENTINEL_BASHRC)
    (tmp_path / "colored-directory").mkdir()
    (tmp_path / "colored.txt").write_text("needle\n")
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "NO_COLOR",
            "FORCE_COLOR",
            "CLICOLOR",
            "CLICOLOR_FORCE",
            "LS_COLORS",
            "LSCOLORS",
            "GIT_CONFIG_COUNT",
            "GIT_CONFIG_PARAMETERS",
        }
    }
    env.update(TERM="xterm-256color", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    if no_color:
        env["NO_COLOR"] = "1"
    args = ["/bin/bash", "--noprofile", "--rcfile", str(rc), "-ic", command]
    if terminal:
        master, slave = pty.openpty()
        try:
            subprocess.run(
                args,
                cwd=tmp_path,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=slave,
                stderr=subprocess.PIPE,
                timeout=10,
            )
            output = bytearray()
            # macOS may discard unread PTY output when the last slave closes.
            while select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                output.extend(chunk)
        finally:
            os.close(master)
            if slave >= 0:
                os.close(slave)
    else:
        output = subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, timeout=10).stdout
    assert output, "The test command must produce output"
    assert (b"\x1b[" in output) is (terminal and not no_color)
