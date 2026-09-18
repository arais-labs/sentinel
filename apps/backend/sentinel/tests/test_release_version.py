"""Release commands update app versions without changing dependency versions."""

import json
import os
from pathlib import Path
import shutil
import subprocess


def test_release_version_sync_and_drift(tmp_path):
    root = Path(__file__).resolve().parents[4]
    files = [
        "VERSION",
        "MIN_SHELL_VERSION",
        "scripts/sync-version.sh",
        "apps/frontend/sentinel/src/lib/env.ts",
    ]
    for folder in ["apps/backend/sentinel", "apps/tui"]:
        files.extend(f"{folder}/{name}" for name in ["pyproject.toml", "uv.lock"])
    for folder in ["apps/frontend/sentinel", "apps/desktop/sentinel"]:
        files.extend(f"{folder}/{name}" for name in ["package.json", "package-lock.json"])
    for name in files:
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, dest)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text('#!/bin/sh\n[ "$*" = "show HEAD:VERSION" ] || exit 1\nprintf "3.2.1\\n"\n')
    fake_git.chmod(0o755)

    def run(*args):
        return subprocess.run(
            ["bash", "scripts/sync-version.sh", *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]},
        )

    original = {name: (tmp_path / name).read_bytes() for name in files}
    assert run("--set", "invalid").returncode != 0
    assert all((tmp_path / name).read_bytes() == content for name, content in original.items())
    result = run("--set", "3.2.1")
    assert result.returncode == 0, result.stderr
    assert run("--check").returncode == 0
    for folder in ["apps/frontend/sentinel", "apps/desktop/sentinel"]:
        lock = json.loads((tmp_path / folder / "package-lock.json").read_text())
        old = json.loads(original[f"{folder}/package-lock.json"])
        assert lock["version"] == lock["packages"][""]["version"] == "3.2.1"
        assert {k: v for k, v in lock["packages"].items() if k} == {
            k: v for k, v in old["packages"].items() if k
        }
    for folder, name in [
        ("apps/backend/sentinel", "sentinel-backend"),
        ("apps/tui", "sentinel-tui"),
    ]:
        lock = (tmp_path / folder / "uv.lock").read_text()
        assert f'name = "{name}"\nversion = "3.2.1"' in lock
        assert 'name = "sentral-runtime"\nversion = "0.1.0"' in lock

    lock_path = tmp_path / "apps/desktop/sentinel/package-lock.json"
    lock_path.write_text(
        lock_path.read_text().replace('"version": "3.2.1"', '"version": "3.2.0"', 1)
    )
    before = lock_path.read_bytes()
    assert run("--check").returncode != 0
    assert lock_path.read_bytes() == before
    assert run().returncode == 0
    assert run("--check").returncode == 0

    assert run("--check", "--greater-than", "HEAD").returncode != 0
    assert run("--set", "3.2.0").returncode == 0
    assert run("--check", "--greater-than", "HEAD").returncode != 0
    assert run("--set", "3.10.0").returncode == 0
    assert run("--check", "--greater-than", "HEAD").returncode == 0
