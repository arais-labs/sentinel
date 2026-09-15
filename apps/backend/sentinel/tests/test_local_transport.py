from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path

import pytest

from app.services.runtime.local_transport import LocalTransport


@pytest.mark.asyncio
async def test_run_returns_stdout_and_exit() -> None:
    res = await LocalTransport().run("echo hello")
    assert res.exit_status == 0
    assert res.stdout.strip() == "hello"


@pytest.mark.asyncio
async def test_run_respects_cwd_and_env(tmp_path: Path) -> None:
    transport = LocalTransport()
    res = await transport.run("pwd", cwd=str(tmp_path))
    assert Path(res.stdout.strip()).resolve() == tmp_path.resolve()
    res_env = await transport.run('printf "%s" "$FOO"', env={"FOO": "bar"})
    assert res_env.stdout == "bar"


@pytest.mark.asyncio
async def test_run_reports_nonzero_exit() -> None:
    res = await LocalTransport().run("exit 3")
    assert res.exit_status == 3


@pytest.mark.asyncio
async def test_run_times_out() -> None:
    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        await LocalTransport().run("sleep 5", timeout=1)


@pytest.mark.asyncio
async def test_timeout_kills_child_processes(tmp_path: Path) -> None:
    # A backgrounded grandchild that writes a marker after the timeout fires.
    marker = tmp_path / "survived"
    cmd = f'( sleep 2; : > "{marker}" ) & sleep 5'
    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        await LocalTransport().run(cmd, timeout=1)
    await asyncio.sleep(3)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_run_script_passes_args_over_stdin() -> None:
    res = await LocalTransport().run_script('printf "%s-%s" "$1" "$2"', args=["a", "b"])
    assert res.exit_status == 0
    assert res.stdout == "a-b"


@pytest.mark.asyncio
async def test_backend_secrets_do_not_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "topsecret")
    monkeypatch.setenv("JWT_SECRET_KEY", "alsosecret")
    res = await LocalTransport().run(
        'printf "%s|%s" "${DATA_ENCRYPTION_KEY-_}" "${JWT_SECRET_KEY-_}"'
    )
    assert res.stdout == "_|_"  # both unset in the command's env


@pytest.mark.skipif(sys.platform != "darwin", reason="PATH ordering relies on macOS path_helper")
@pytest.mark.asyncio
async def test_login_shell_relegates_bundled_path_to_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    # A marker dir seeded at the front of the backend PATH must end up behind /usr/bin.
    marker = "/zz-bundled-marker-bin"
    monkeypatch.setenv("PATH", f"{marker}:{os.environ.get('PATH') or '/usr/bin:/bin'}")
    res = await LocalTransport().run('printf "%s" "$PATH"')
    path = res.stdout
    assert "/usr/bin" in path
    assert marker in path
    assert path.index("/usr/bin") < path.index(marker)


@pytest.mark.asyncio
async def test_create_process_streams_lines() -> None:
    proc = await LocalTransport().create_process("printf 'l1\\nl2\\n'", encoding="utf-8")
    line1 = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
    line2 = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
    assert line1 == "l1\n"
    assert line2 == "l2\n"
    await asyncio.wait_for(proc.wait(), timeout=5)
    assert proc.exit_status == 0


@pytest.mark.asyncio
async def test_create_process_handles_long_lines() -> None:
    # A single line well past asyncio's 64 KiB default line buffer.
    width = 200_000
    proc = await LocalTransport().create_process(
        f"printf 'x%.0s' $(seq 1 {width}); printf '\\n'", encoding="utf-8"
    )
    line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
    assert line == "x" * width + "\n"
    await asyncio.wait_for(proc.wait(), timeout=5)


@pytest.mark.asyncio
async def test_create_process_merges_stderr_into_stdout() -> None:
    proc = await LocalTransport().create_process(
        "printf 'to-out\\n'; printf 'to-err\\n' 1>&2", encoding="utf-8"
    )
    assert proc.stderr is None

    lines = set()
    for _ in range(2):
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
        lines.add(line.strip())
    await asyncio.wait_for(proc.wait(), timeout=5)
    assert lines == {"to-out", "to-err"}


@pytest.mark.asyncio
async def test_forward_local_port_relays_to_target() -> None:
    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        data = await reader.read(100)
        writer.write(data)
        await writer.drain()
        writer.close()

    target = await asyncio.start_server(echo, "127.0.0.1", 0)
    target_port = target.sockets[0].getsockname()[1]

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    listen_port = probe.getsockname()[1]
    probe.close()

    fwd = await LocalTransport().forward_local_port(
        "127.0.0.1", listen_port, "127.0.0.1", target_port
    )
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", listen_port)
        writer.write(b"ping")
        await writer.drain()
        writer.write_eof()
        got = await asyncio.wait_for(reader.read(100), timeout=3)
        assert got == b"ping"
        writer.close()
    finally:
        fwd.close()
        await fwd.wait_closed()
        target.close()
        await target.wait_closed()
