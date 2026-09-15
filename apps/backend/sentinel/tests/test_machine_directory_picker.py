import subprocess

from app.services.runtime.guest_commands import load_guest_command


def test_directory_picker_preserves_names_and_only_lists_folders(tmp_path):
    names = ["normal", ".hidden", "space and apostrophe's", "line\nbreak", "$(touch bad)"]
    for name in names:
        (tmp_path / name).mkdir()
    (tmp_path / "file.txt").write_text("not a directory")
    script = load_guest_command("common/directories.sh")
    result = subprocess.run(
        ["/bin/sh", "-c", script, "picker", str(tmp_path)], capture_output=True, check=True
    )
    fields = result.stdout.decode().split("\0")
    assert fields[0] == str(tmp_path.resolve())
    assert set(fields[1:-1]) == set(names)
    assert not (tmp_path / "bad").exists()


def test_directory_picker_missing_path_fails(tmp_path):
    result = subprocess.run(
        [
            "/bin/sh",
            "-c",
            load_guest_command("common/directories.sh"),
            "picker",
            str(tmp_path / "missing"),
        ],
        capture_output=True,
    )
    assert result.returncode != 0
    assert not result.stdout


def test_local_picker_matches_shell_listing_without_starting_processes(tmp_path, monkeypatch):
    from app.services.runtime.directories import list_local_directories

    (tmp_path / "folder with spaces").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "file.txt").write_text("file")
    (tmp_path / "link").symlink_to(tmp_path / ".hidden", target_is_directory=True)

    def no_process(*args, **kwargs):
        raise AssertionError("Local folder browsing must not start a shell")

    monkeypatch.setattr(subprocess, "Popen", no_process)
    listing = list_local_directories(str(tmp_path))
    assert listing == {
        "path": str(tmp_path.resolve()),
        "parent": str(tmp_path.resolve().parent),
        "directories": [".hidden", "folder with spaces", "link"],
    }


def test_local_picker_invalid_path_errors(tmp_path):
    import pytest

    from app.services.runtime.directories import list_local_directories

    with pytest.raises(FileNotFoundError):
        list_local_directories(str(tmp_path / "missing"))


def test_create_local_folder_preserves_literal_name_and_rejects_existing(tmp_path):
    import pytest

    from app.services.runtime.directories import create_local_directory

    name = "my project's $(touch marker)"
    result = create_local_directory(str(tmp_path), name)
    assert result["path"] == str(tmp_path / name)
    assert result["parent"] == str(tmp_path)
    assert (tmp_path / name).is_dir()
    assert not (tmp_path / "marker").exists()
    with pytest.raises(FileExistsError):
        create_local_directory(str(tmp_path), name)
    (tmp_path / "file").write_text("keep")
    with pytest.raises(FileExistsError):
        create_local_directory(str(tmp_path), "file")
    assert (tmp_path / "file").read_text() == "keep"


def test_create_folder_rejects_traversal_and_missing_parent(tmp_path):
    import pytest

    from app.services.runtime.directories import create_local_directory

    for name in ["", " ", ".", "..", "../escape", "a/b", "a\\b", "a\x00b", "a\nb"]:
        with pytest.raises(ValueError):
            create_local_directory(str(tmp_path), name)
    with pytest.raises(ValueError):
        create_local_directory("relative", "new")
    with pytest.raises(FileNotFoundError):
        create_local_directory(str(tmp_path / "missing"), "new")
    assert list(tmp_path.iterdir()) == []


def test_remote_create_folder_handles_quotes_and_conflicts(tmp_path):
    from shlex import quote

    name = "-my project's $(touch marker)"
    command = f"set -- {quote(str(tmp_path))} {quote(name)}\n" + load_guest_command(
        "common/create_directory.sh"
    )
    result = subprocess.run(["/bin/sh", "-c", command], capture_output=True)
    assert result.returncode == 0
    assert result.stdout.decode().rstrip("\n") == str(tmp_path / name)
    assert (tmp_path / name).is_dir()
    assert not (tmp_path / "marker").exists()
    again = subprocess.run(["/bin/sh", "-c", command], capture_output=True)
    assert again.returncode == 17


def test_create_directory_endpoint_local_errors(tmp_path, monkeypatch):
    import asyncio
    import uuid
    from types import SimpleNamespace

    import pytest
    from fastapi import HTTPException

    from app.routers import machines as router
    from app.services.runtime import machines

    async def get_machine(*args):
        return SimpleNamespace(provider="local")

    monkeypatch.setattr(router.machines_module, "get_machine", get_machine)
    monkeypatch.setattr(machines, "resolve_machine_secret", lambda value: value)
    payload = router.DirectoryCreateRequest(path=str(tmp_path), name="new project")
    result = asyncio.run(router.create_machine_directory(uuid.uuid4(), payload, None))
    assert result["path"] == str(tmp_path / "new project")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(router.create_machine_directory(uuid.uuid4(), payload, None))
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            router.create_machine_directory(
                uuid.uuid4(),
                router.DirectoryCreateRequest(path=str(tmp_path), name="../escape"),
                None,
            )
        )
    assert exc.value.status_code == 422
