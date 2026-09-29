"""Qualify native Alpine LocalSearch extraction and indexing as the GUI user.

The installed CLI applies Landlock before spawning its extractor. This fixture
uses that public CLI and the existing session bus, never a replacement daemon
or sandbox-disabling environment. All temporary indexing settings are restored.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid


def assert_unpinned(world):
    entries = [
        line.strip()
        for line in world.splitlines()
        if re.match(r"!?localsearch(?:$|[@<>=~])", line.strip())
    ]
    assert entries == ["localsearch"], f"LocalSearch package remains pinned: {entries}"


def content_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from content_values(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from content_values(item)


def extracted_text(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith((":plainTextContent", "#plainTextContent")):
                yield from content_values(item)
            else:
                yield from extracted_text(item)
    elif isinstance(value, list):
        for item in value:
            yield from extracted_text(item)


def assert_content_extracted(metadata, token):
    assert any(
        token in text for text in extracted_text(metadata)
    ), "Sandboxed extraction did not return the document's unique text content"


def main():
    session = json.loads(Path("/run/sentinel-desktop/session.json").read_text())
    user = session["user"]
    assert user["uid"] > 0 and user["gid"] > 0
    assert Path("/etc/sentinel/desktop-choice").read_text().strip() == "gnome"
    world_path = Path("/etc/apk/world")
    original_world = world_path.read_text()
    assert_unpinned(original_world)
    subprocess.run(
        ["apk", "info", "--installed", "localsearch"],
        check=True,
        stdout=subprocess.DEVNULL,
        timeout=5,
    )
    installed_package = subprocess.check_output(
        ["apk", "list", "--installed", "localsearch"], text=True, timeout=5
    ).strip()
    assert installed_package.startswith("localsearch-"), installed_package

    os.environ.clear()
    os.environ.update(session["environment"])
    os.environ["LC_ALL"] = "C"
    os.setgroups(user["groups"])
    os.setgid(user["gid"])
    os.setuid(user["uid"])
    os.chdir(user["home"])
    assert os.getuid() == user["uid"]

    from gi.repository import Gio

    settings = Gio.Settings.new("org.freedesktop.Tracker3.Miner.Files")
    keys = ("index-single-directories", "index-recursive-directories")
    original = {key: settings.get_user_value(key) for key in keys}
    directory = Path(tempfile.mkdtemp(prefix="sentinel-localsearch-", dir=user["home"]))
    document = directory / "document.txt"
    # Keep the unique word short and alphabetic so tokenizer word-length limits
    # and numeric token boundaries cannot turn this into a filename-only test.
    token = "sentinel" + uuid.uuid4().hex[:16].translate(str.maketrans("0123456789", "ghijklmnop"))
    assert token not in str(document)
    document.write_text(f"Native sandboxed desktop indexing.\nUnique content: {token}\n")
    assert document.stat().st_uid == user["uid"]

    def command(*arguments, timeout=10):
        result = subprocess.run(
            ["localsearch", *arguments], capture_output=True, text=True, timeout=timeout
        )
        assert result.returncode == 0, (
            f"LocalSearch {arguments!r} exited {result.returncode}: "
            f"stdout={result.stdout[-4096:]!r}; stderr={result.stderr[-4096:]!r}"
        )
        return result.stdout

    try:
        metadata = json.loads(command("extract", "--output-format=json-ld", str(document)))
        assert_content_extracted(metadata, token)
        command("index", "--add", str(directory))
        deadline = time.monotonic() + 45
        attempts = 0
        while True:
            attempts += 1
            results = command("search", "--documents", "--limit=100", token, timeout=5)
            if document.as_uri() in results.splitlines():
                break
            assert (
                time.monotonic() < deadline
            ), f"Document content never reached the native LocalSearch index: {results!r}"
            time.sleep(0.2)
    finally:
        # Restore exact user values, including unset/default values. Merely
        # removing our directory would leave a newly materialized default list.
        for key, value in original.items():
            settings.reset(key) if value is None else settings.set_value(key, value)
        Gio.Settings.sync()
        shutil.rmtree(directory)
        assert world_path.read_text() == original_world, "Indexing changed APK world"
        for key, value in original.items():
            restored = settings.get_user_value(key)
            assert (
                restored is None
                if value is None
                else restored is not None and restored.equal(value)
            ), f"LocalSearch indexing setting was not restored: {key}"
    print(
        json.dumps(
            {
                "uid": os.getuid(),
                "installed_package": installed_package,
                "world_unpinned": True,
                "sandboxed_extraction": True,
                "content_indexed": True,
                "query_attempts": attempts,
                "settings_restored": True,
            }
        )
    )


if __name__ == "__main__":
    main()
