import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.services import pull_request_review as service
from app.routers.pull_request_review import Review


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/o/r/pull/1",
        "https://evil.test/o/r/pull/1",
        "https://github.com@evil.test/o/r/pull/1",
        "https://github.com/o/../pull/1",
        "https://github.com/o/r/pull/1?next=evil",
    ],
)
def test_pr_url_rejects_other_destinations(url):
    with pytest.raises(HTTPException):
        service.parse_pr(url)


def test_pr_url_and_scope():
    assert service.parse_pr("https://github.com/o/r/pull/123") == ("o", "r", 123)
    account = SimpleNamespace(scope_pattern="github.com/o/*")
    assert service.in_scope(account, "o", "r")
    assert not service.in_scope(account, "other", "r")


def install_remote(monkeypatch, callback):
    def client(account):
        return httpx.AsyncClient(
            base_url="https://api.github.com",
            transport=httpx.MockTransport(callback),
            headers={"Authorization": "Bearer test-only"},
        )

    monkeypatch.setattr(service, "client", client)


@pytest.mark.asyncio
async def test_load_stream_progress_data_and_partial_check_failure(monkeypatch):
    calls = []

    def upstream(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/pulls/1"):
            return httpx.Response(
                200, json={"number": 1, "head": {"sha": "a" * 40}, "changed_files": 1}
            )
        if request.url.path.endswith("/files"):
            return httpx.Response(
                200, json=[{"filename": "file.py", "patch": "@@ -1 +1 @@\n-a\n+b", "sha": "f"}]
            )
        if request.url.path.endswith("/check-runs"):
            return httpx.Response(403)
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"statuses": []})
        return httpx.Response(200, json=[])

    install_remote(monkeypatch, upstream)
    events = [
        json.loads(row)
        async for row in service.load_review(SimpleNamespace(id=uuid4()), "o", "r", 1)
    ]
    assert events[0]["event"] == "progress"
    assert next(e for e in events if e["event"] == "files")["data"][0]["filename"] == "file.py"
    assert any(e["event"] == "warning" for e in events)
    assert events[-1]["event"] == "complete"
    assert calls.count("/repos/o/r/pulls/1") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", [True, False])
async def test_submit_binds_review_to_checked_commit(monkeypatch, stale):
    posted = []

    def upstream(request):
        if request.method == "GET":
            return httpx.Response(
                200, json={"state": "open", "head": {"sha": ("b" if stale else "a") * 40}}
            )
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"id": 1, "state": "COMMENTED"})

    install_remote(monkeypatch, upstream)
    payload = Review(
        url="https://github.com/o/r/pull/1",
        commit_id="a" * 40,
        event="COMMENT",
        comments=[
            {"path": "file.py", "line": 2, "side": "RIGHT", "body": "Please handle empty input."}
        ],
    )
    events = [json.loads(row) async for row in service.submit_review(None, "o", "r", 1, payload)]
    assert events[-1]["event"] == ("error" if stale else "submitted")
    assert len(posted) == (0 if stale else 1)
    if posted:
        assert posted[0]["commit_id"] == "a" * 40
        assert posted[0]["comments"][0]["line"] == 2


@pytest.mark.asyncio
async def test_uncertain_submission_is_not_retried(monkeypatch):
    writes = []

    def upstream(request):
        if request.method == "GET":
            return httpx.Response(200, json={"state": "open", "head": {"sha": "a" * 40}})
        writes.append(1)
        raise httpx.ReadTimeout("timeout")

    install_remote(monkeypatch, upstream)
    payload = Review(url="https://github.com/o/r/pull/1", commit_id="a" * 40, event="APPROVE")
    events = [json.loads(row) async for row in service.submit_review(None, "o", "r", 1, payload)]
    assert len(writes) == 1
    assert "unknown" in events[-1]["message"]


@pytest.mark.asyncio
async def test_check_rejects_old_commit(monkeypatch):
    def upstream(request):
        return httpx.Response(
            200, json={"head": {"sha": "a"}} if "/pulls/" in request.url.path else {"head_sha": "b"}
        )

    install_remote(monkeypatch, upstream)
    events = [json.loads(row) async for row in service.check_progress(None, "o", "r", 1, 4)]
    assert events[-1]["event"] == "error"
    assert "older commit" in events[-1]["message"]


@pytest.mark.asyncio
async def test_live_job_steps_then_logs_without_token(monkeypatch):
    real_client = httpx.AsyncClient
    calls = []
    job_polls = 0

    def upstream(request):
        nonlocal job_polls
        calls.append(request)
        path = request.url.path
        if "/pulls/" in path:
            return httpx.Response(200, json={"head": {"sha": "a"}})
        if "/check-runs/" in path:
            return httpx.Response(
                200,
                json={
                    "head_sha": "a",
                    "details_url": "https://github.com/o/r/actions/runs/1/job/2",
                },
            )
        if path.endswith("/logs"):
            return httpx.Response(
                302, headers={"location": "https://logs.blob.core.windows.net/job"}
            )
        if request.url.host == "logs.blob.core.windows.net":
            assert "authorization" not in request.headers
            return httpx.Response(200, text="step output\nfinished\n")
        job_polls += 1
        return httpx.Response(
            200,
            json={
                "status": "in_progress" if job_polls == 1 else "completed",
                "steps": [{"name": "Build", "status": "completed"}],
            },
        )

    transport = httpx.MockTransport(upstream)
    monkeypatch.setattr(
        service,
        "client",
        lambda account: real_client(
            base_url="https://api.github.com",
            transport=transport,
            headers={"Authorization": "Bearer test-secret"},
        ),
    )
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs)
    )
    import asyncio

    async def no_wait(seconds):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    events = [json.loads(row) async for row in service.check_progress(None, "o", "r", 1, 4)]
    assert len([e for e in events if e["event"] == "job"]) == 2
    assert next(e for e in events if e["event"] == "log")["data"] == "step output\nfinished\n"
    assert events[-1]["event"] == "complete"
