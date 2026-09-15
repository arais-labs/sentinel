from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.runtime.guest_commands import load_guest_python


@pytest.fixture
def file_runtime(tmp_path, monkeypatch):
    # Commit hooks export repository-local Git variables (notably GIT_INDEX_FILE).
    # Temporary test repositories must discover their own indexes and worktrees.
    for name in subprocess.check_output(
        ["git", "rev-parse", "--local-env-vars"], text=True
    ).splitlines():
        monkeypatch.delenv(name, raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "container-home"
    outside.mkdir()
    script = load_guest_python("common/files/operations.py")

    def call(operation, **payload):
        request = {
            "operation": operation,
            "workspace": str(workspace),
            "session_id": "test",
            "session_root": str(tmp_path),
            "payload": payload,
        }
        # Exercise the shipped command against test-owned folders. Production
        # executes this same command through ContainerTransport, never on the host.
        result = subprocess.run(
            [sys.executable, "-c", script, json.dumps(request)],
            env={**os.environ, "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}"},
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return json.loads(result.stdout)

    return workspace, outside, call


@pytest.mark.parametrize("style", ["relative", "absolute", "parent", "symlink"])
def test_read_and_replace_use_container_paths(file_runtime, style):
    workspace, outside, call = file_runtime
    target = (workspace if style in {"relative", "absolute"} else outside) / "app.py"
    target.write_text("before\n")
    (workspace / "home-link").symlink_to(outside, target_is_directory=True)
    path = {
        "relative": "app.py",
        "absolute": str(target),
        "parent": "../container-home/app.py",
        "symlink": "home-link/app.py",
    }[style]
    assert call("preview_file", path=path)["data"]["content"] == "before\n"
    edited = call("str_replace", path=path, old_str="before", new_str="after")
    assert edited["ok"] is True
    assert target.read_text() == "after\n"
    # Every returned path can be passed straight back into another file operation.
    assert call("preview_file", path=edited["data"]["path"])["data"]["content"] == "after\n"


def test_list_and_download_outside_project_keep_absolute_paths(file_runtime):
    _, outside, call = file_runtime
    (outside / "config.txt").write_text("settings")
    (outside / "broken-link").symlink_to(outside / "missing")
    listing = call("list_files", path=str(outside))["data"]
    assert listing["path"] == str(outside)
    assert listing["parent_path"] == str(outside.parent)
    assert listing["entries"][0]["path"] == str(outside / "config.txt")


def test_project_listing_retains_relative_paths(file_runtime):
    workspace, _, call = file_runtime
    (workspace / "src").mkdir()
    listing = call("list_files")["data"]
    assert listing["path"] == ""
    assert listing["parent_path"] is None
    assert listing["entries"][0]["path"] == "src"
    assert call("list_files", path=str(workspace / "src"))["data"]["parent_path"] == ""
    root = call("list_files", path="/", limit=1)["data"]
    assert root["path"] == "/"
    assert root["parent_path"] is None


def test_linux_filenames_are_not_rewritten(file_runtime):
    workspace, _, call = file_runtime
    name = " back\\slash name "
    (workspace / name).write_text("exact")
    assert call("preview_file", path=name)["data"]["content"] == "exact"
    assert call("preview_file", path="bad\0path")["error"] == "invalid_path"


def test_preview_limits_reads_and_replacement_remains_unique(file_runtime):
    _, outside, call = file_runtime
    target = outside / "large.txt"
    target.write_text("a" * 1000)
    preview = call("preview_file", path=str(target), max_bytes=256)["data"]
    assert preview["truncated"] is True
    assert preview["content"] == "a" * 256
    assert preview["size_bytes"] == 1000
    assert call("str_replace", path=str(target), old_str="a", new_str="b")["ok"] is False
    assert target.read_text() == "a" * 1000


def test_git_paths_outside_project_round_trip(file_runtime):
    _, repo, call = file_runtime

    def git(*args):
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            check=True,
            capture_output=True,
        )

    git("init")
    target = repo / "app.txt"
    target.write_text("before\n")
    git("add", "app.txt")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial")
    target.write_text("after\n")
    roots = call("git_roots", path=str(repo))["data"]["roots"]
    assert roots[0]["root_path"] == str(repo)
    changes = call("git_changed", path=str(repo))["data"]
    assert changes["entries"][0]["path"] == str(target)
    diff = call("git_diff", path=changes["entries"][0]["path"])["data"]
    assert "-before" in diff["diff"] and "+after" in diff["diff"]


def git_at(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout


def init_browser_repo(root):
    git_at(root, "init", "-b", "main")
    git_at(root, "config", "user.name", "Browser Test")
    git_at(root, "config", "user.email", "browser@example.invalid")


def test_browser_light_context_and_status_do_not_scan_other_worktrees(file_runtime, monkeypatch):
    workspace, outside, call = file_runtime
    init_browser_repo(workspace)
    (workspace / "app.txt").write_text("before\n")
    git_at(workspace, "add", ".")
    git_at(workspace, "commit", "-m", "Initial")
    for index in range(3):
        git_at(workspace, "worktree", "add", "-b", f"feature-{index}", str(outside / str(index)))
    (workspace / "app.txt").write_text("after\n")
    trace = outside / "git.trace"
    monkeypatch.setenv("GIT_TRACE", str(trace))

    light = call("git_context", include_worktrees=False)["data"]
    commands = trace.read_text()
    assert commands.count("rev-parse --show-toplevel") == 1
    assert "worktree list" not in commands
    assert light["repository"]["branch"] == "main"
    assert len(light["repository"]["refs"]) == 4
    assert light["worktrees"] == []

    trace.write_text("")
    status = call("git_changed")["data"]
    commands = trace.read_text()
    assert "worktree list" not in commands
    assert "for-each-ref" not in commands
    assert commands.count("status --porcelain") == 1
    assert status["entries"][0]["path"] == "app.txt"

    # Diff already resolves the file's repository, including nested checkouts.
    # Return its refs with the diff so opening it needs no context request.
    diff = call("git_diff", path="app.txt")["data"]
    assert diff["repository"]["refs"] == light["repository"]["refs"]
    assert diff["repository"]["root_path"] == ""
    assert "+after" in diff["diff"]
    full = call("git_context")["data"]
    assert len(full["worktrees"]) == 4
    assert all(tree["available"] for tree in full["worktrees"])


def test_browser_root_repo_unborn_and_real_refs(file_runtime):
    workspace, _, call = file_runtime
    init_browser_repo(workspace)
    root = call("git_roots")["data"]["roots"]
    assert len(root) == 1
    assert root[0]["root_path"] == ""
    assert root[0]["branch"] == "main"
    assert call("git_history")["data"]["commits"] == []
    (workspace / "new.txt").write_text("first\n")
    git_at(workspace, "add", ".")
    assert "+first" in call("git_diff", path="new.txt", staged=True)["data"]["diff"]
    git_at(workspace, "commit", "-m", "First commit")
    git_at(workspace, "branch", "feature/files")
    context = call("git_context")["data"]
    assert set(context["repository"]["refs"]) == {"main", "feature/files"}
    assert context["worktrees"][0]["current"] is True
    assert context["worktrees"][0]["available"] is True
    assert call("git_history")["data"]["commits"][0]["subject"] == "First commit"


def test_browser_preserves_paths_and_separates_index(file_runtime):
    workspace, _, call = file_runtime
    init_browser_repo(workspace)
    name = " spaced -> name\n.txt "
    (workspace / name).write_text("original\n")
    git_at(workspace, "add", ".")
    git_at(workspace, "commit", "-m", "Original")
    (workspace / name).write_text("staged\n")
    git_at(workspace, "add", ".")
    (workspace / name).write_text("working\n")
    (workspace / "untracked.txt").write_text("untracked\n")
    changes = call("git_changed")["data"]["entries"]
    changed = next(item for item in changes if item["path"] == name)
    assert changed["status"] == "MM"
    assert changed["staged"] and changed["unstaged"]
    staged = call("git_diff", path=name, staged=True)["data"]["diff"]
    working = call("git_diff", path=name, base_ref="")["data"]["diff"]
    assert "-original" in staged and "+staged" in staged and "+working" not in staged
    assert "-staged" in working and "+working" in working
    assert call("git_diff", path="untracked.txt", staged=True)["data"]["diff"] == ""
    assert "+untracked" in call("git_diff", path="untracked.txt")["data"]["diff"]
    git_at(workspace, "reset", "--hard", "HEAD")
    git_at(workspace, "mv", name, " renamed.txt ")
    renamed = next(
        item for item in call("git_changed")["data"]["entries"] if item["status"].startswith("R")
    )
    assert renamed["path"] == " renamed.txt "
    assert renamed["original_path"] == name


def test_browser_discovers_nested_linked_worktree_and_missing_metadata(file_runtime):
    workspace, _, call = file_runtime
    repo = workspace / "repo"
    repo.mkdir()
    init_browser_repo(repo)
    (repo / "a").write_text("a")
    git_at(repo, "add", ".")
    git_at(repo, "commit", "-m", "Base")
    linked = workspace / "linked"
    git_at(repo, "worktree", "add", "-b", "feature", str(linked))
    roots = call("git_roots")["data"]["roots"]
    assert {root["root_path"] for root in roots} == {"repo", "linked"}
    context = call("git_context", path="linked")["data"]
    assert context["repository"]["branch"] == "feature"
    assert len(context["worktrees"]) == 2
    assert all(tree["available"] for tree in context["worktrees"])
    assert next(tree for tree in context["worktrees"] if tree["current"])["path"] == "linked"
    broken = workspace / "broken"
    broken.mkdir()
    (broken / ".git").write_text("gitdir: /missing-on-this-machine/worktrees/broken\n")
    assert "metadata" in call("git_context", path="broken")["detail"]
    assert any(error["path"] == "broken" for error in call("git_roots")["data"]["errors"])


def test_browser_search_skips_generated_folders(file_runtime):
    workspace, _, call = file_runtime
    (workspace / "src").mkdir()
    (workspace / "src" / "app.ts").write_text("source")
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "app.ts").write_text("dependency")
    result = call("search_files", query="APP.TS")["data"]
    assert [item["path"] for item in result["entries"]] == ["src/app.ts"]


def test_repository_picker_groups_worktrees_and_excludes_generated_repositories(file_runtime):
    workspace, _, call = file_runtime
    repo = workspace / "project"
    repo.mkdir()
    init_browser_repo(repo)
    (repo / "a").write_text("a")
    git_at(repo, "add", ".")
    git_at(repo, "commit", "-m", "Base")
    linked = workspace / "linked"
    git_at(repo, "worktree", "add", "-b", "feature", str(linked))
    dependency = repo / ".terraform" / "modules" / "dependency"
    dependency.mkdir(parents=True)
    init_browser_repo(dependency)
    roots = call("git_roots", group_worktrees=True)["data"]["roots"]
    assert [root["root_path"] for root in roots] == ["project"]
    context = call("git_context", path="linked")["data"]
    assert context["repository"]["common_dir"] == roots[0]["common_dir"]
    assert len(context["worktrees"]) == 2
    expected_date = int(git_at(repo, "log", "-1", "--format=%ct").strip())
    assert all(tree["last_commit_at"] == expected_date for tree in context["worktrees"])
    included = call("git_roots", group_worktrees=True, include_generated=True)["data"]["roots"]
    assert {root["root_path"] for root in included} == {
        "project",
        "project/.terraform/modules/dependency",
    }
    # The session Git-root API still exposes individual worktrees.
    assert {root["root_path"] for root in call("git_roots")["data"]["roots"]} == {
        "project",
        "linked",
    }


def test_changed_tree_can_open_deleted_file_diff(file_runtime):
    workspace, _, call = file_runtime
    init_browser_repo(workspace)
    target = workspace / "src" / "deleted.py"
    target.parent.mkdir()
    target.write_text("print('before')\n")
    git_at(workspace, "add", ".")
    git_at(workspace, "commit", "-m", "Base")
    target.unlink()
    changes = call("git_changed")["data"]["entries"]
    assert changes[0]["path"] == "src/deleted.py"
    assert changes[0]["status"] == " D"
    diff = call("git_diff", path=changes[0]["path"], base_ref="")["data"]["diff"]
    assert "-print('before')" in diff


@pytest.mark.parametrize(
    "before",
    [
        "café\r\n\told\r\nfin\r\n",
        "café\n\told\nfin\n",
        "café\r\told\rfin\r",
        "café\r\n\told\nfin\r",
        "café\r\n\told",
        "\ufeffcafé\r\n\told\r\n",
    ],
)
def test_replace_preserves_exact_bytes_outside_match(file_runtime, before):
    workspace, _, call = file_runtime
    target = workspace / "text.txt"
    target.write_bytes(before.encode("utf-8"))
    target.chmod(0o750)
    result = call("str_replace", path="text.txt", old_str="old", new_str="prêt ✓")
    assert result["ok"] is True
    expected = before.replace("old", "prêt ✓").encode("utf-8")
    assert target.read_bytes() == expected
    assert result["data"]["size_bytes"] == len(expected)
    assert target.stat().st_mode & 0o777 == 0o750


def test_replace_exact_multiline_crlf(file_runtime):
    workspace, _, call = file_runtime
    target = workspace / "text.txt"
    target.write_bytes(b"head\r\nold\r\nblock\r\ntail")
    assert (
        call("str_replace", path="text.txt", old_str="old\r\nblock", new_str="new\r\nblock")["ok"]
        is True
    )
    assert target.read_bytes() == b"head\r\nnew\r\nblock\r\ntail"


@pytest.mark.parametrize(
    ("before", "old"),
    [
        (b"same\r\nsame\r\n", "same"),
        (b"aaa\r\n", "aa"),
        (b"old\r\nblock\r\n", "old\nblock"),
        (b"old\xff\r\n", "old"),
    ],
)
def test_rejected_replace_leaves_bytes_unchanged(file_runtime, before, old):
    workspace, _, call = file_runtime
    target = workspace / "text.txt"
    target.write_bytes(before)
    assert call("str_replace", path="text.txt", old_str=old, new_str="new")["ok"] is False
    assert target.read_bytes() == before


@pytest.mark.parametrize("name", ["index.ts", "index.tsx", "index.mts", "index.cts"])
def test_typescript_files_remain_source_previews(file_runtime, name):
    workspace, _, call = file_runtime
    (workspace / name).write_text("export const value = 1;\n")
    preview = call("preview_file", path=name)["data"]
    assert preview["media_type"] == "text/typescript"
    assert preview["binary"] is False
    assert preview["content"] == "export const value = 1;\n"
