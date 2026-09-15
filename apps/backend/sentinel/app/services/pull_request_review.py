"""GitHub review transport. Credentials remain in the backend."""

import asyncio
import fnmatch
import json
import re
from urllib.parse import urlparse, urlsplit
from uuid import UUID

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from app.models import GitAccount
from app.services.secrets import is_invalid_secret


def parse_pr(value: str) -> tuple[str, str, int]:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "github.com"
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(
            422, "Use a GitHub pull request URL: https://github.com/owner/repo/pull/123"
        )
    match = re.fullmatch(r"/([\w.-]+)/([\w.-]+)/pull/([1-9]\d*)/?", parsed.path)
    if not match or any(part in {".", ".."} for part in match.groups()[:2]):
        raise HTTPException(422, "That URL does not identify a pull request")
    owner, repo, number = match.groups()
    return owner, repo, int(number)


def in_scope(account, owner, repo):
    pattern = (account.scope_pattern or "*").lower()
    return fnmatch.fnmatch(f"github.com/{owner}/{repo}".lower(), pattern) or fnmatch.fnmatch(
        f"{owner}/{repo}".lower(), pattern
    )


async def choose_account(db, account_id: UUID | None, owner=None, repo=None):
    accounts = (await db.execute(select(GitAccount))).scalars().all()
    accounts = [
        a for a in accounts if a.host == "github.com" and a.token and not is_invalid_secret(a.token)
    ]
    if account_id:
        accounts = [a for a in accounts if a.id == account_id]
    if owner and repo:
        accounts = [a for a in accounts if in_scope(a, owner, repo)]
    if not accounts:
        raise HTTPException(
            403, "Connect a GitHub account with access to this repository in Settings"
        )
    if len(accounts) != 1:
        raise HTTPException(409, "Choose the GitHub account to use for this review")
    return accounts[0]


def client(account):
    return httpx.AsyncClient(
        base_url="https://api.github.com",
        trust_env=False,
        follow_redirects=False,
        timeout=45,
        headers={
            "Authorization": f"Bearer {account.token}",
            "Accept": "application/vnd.github+json",
        },
    )


async def request(client, path, method="GET", **kwargs):
    response = await client.request(method, path, **kwargs)
    if response.status_code >= 300:
        message = {
            401: "GitHub authentication expired. Reconnect your account.",
            403: "GitHub denied access or its rate limit was reached.",
            404: "Pull request or repository not found for this account.",
            422: "GitHub rejected this review. Check the comment locations and your review permissions.",
        }.get(response.status_code, f"GitHub request failed ({response.status_code})")
        raise HTTPException(response.status_code if response.status_code < 500 else 502, message)
    return response.json()


def frame(event, **data):
    return json.dumps({"event": event, **data}) + "\n"


async def load_review(account, owner, repo, number):
    root = f"/repos/{owner}/{repo}"
    pull = f"{root}/pulls/{number}"
    try:
        async with client(account) as remote:
            yield frame("progress", label="Loading pull request", step=1, total=5)
            pr = await request(remote, pull)
            yield frame(
                "pull",
                data={
                    key: pr.get(key)
                    for key in (
                        "number",
                        "title",
                        "body",
                        "html_url",
                        "state",
                        "draft",
                        "merged",
                        "user",
                        "head",
                        "base",
                        "additions",
                        "deletions",
                        "changed_files",
                    )
                },
                account_id=str(account.id),
            )
            yield frame("progress", label="Loading changed files", step=2, total=5)
            count = 0
            for page in range(1, 31):
                files = await request(
                    remote, pull + "/files", params={"per_page": 100, "page": page}
                )
                count += len(files)
                yield frame(
                    "files",
                    data=[
                        {
                            key: f.get(key)
                            for key in (
                                "filename",
                                "previous_filename",
                                "status",
                                "additions",
                                "deletions",
                                "patch",
                                "sha",
                            )
                        }
                        for f in files
                    ],
                )
                if len(files) < 100:
                    break
            if count < pr["changed_files"]:
                yield frame(
                    "warning",
                    message=f'GitHub returned {count} of {pr["changed_files"]} files. This review is incomplete.',
                )
            for step, event, path in [
                (3, "comments", pull + "/comments"),
                (4, "activity", root + f"/issues/{number}/comments"),
                (4, "reviews", pull + "/reviews"),
            ]:
                yield frame("progress", label=f"Loading {event}", step=step, total=5)
                try:
                    for page in range(1, 11):
                        data = await request(remote, path, params={"per_page": 100, "page": page})
                        yield frame(event, data=data)
                        if len(data) < 100:
                            break
                    else:
                        yield frame("warning", message=f"Only the first 1,000 {event} are shown.")
                except HTTPException as exc:
                    yield frame("warning", message=f"Could not load {event}: {exc.detail}")
            yield frame("progress", label="Checking CI status", step=5, total=5)
            for event, path in [
                ("checks", f'{root}/commits/{pr["head"]["sha"]}/check-runs'),
                ("statuses", f'{root}/commits/{pr["head"]["sha"]}/status'),
            ]:
                try:
                    data = await request(remote, path, params={"per_page": 100})
                    yield frame(
                        event, data=data.get("check_runs" if event == "checks" else "statuses", [])
                    )
                    if data.get("total_count", 0) > 100:
                        yield frame(
                            "warning",
                            message=f"Showing the first 100 {event}. Check GitHub for the full list.",
                        )
                except HTTPException as exc:
                    yield frame("warning", message=f"Could not load {event}: {exc.detail}")
            latest = await request(remote, pull)
            if latest["head"]["sha"] != pr["head"]["sha"]:
                yield frame(
                    "error", message="New commits arrived while loading. Refresh before reviewing."
                )
            else:
                yield frame("complete")
    except (HTTPException, httpx.HTTPError) as exc:
        yield frame(
            "error",
            message=(
                str(exc.detail)
                if isinstance(exc, HTTPException)
                else "Connection to GitHub failed. Refresh to try again."
            ),
        )


async def submit_review(account, owner, repo, number, payload):
    path = f"/repos/{owner}/{repo}/pulls/{number}"
    posting = False
    try:
        async with client(account) as remote:
            yield frame("progress", label="Verifying the reviewed commit", step=1, total=2)
            latest = await request(remote, path)
            if latest["head"]["sha"] != payload.commit_id:
                yield frame(
                    "error",
                    message="The PR has new commits. Refresh and review the latest changes before submitting.",
                )
                return
            if latest["state"] != "open":
                yield frame("error", message="This pull request is no longer open.")
                return
            yield frame("progress", label="Submitting your review to GitHub", step=2, total=2)
            posting = True
            data = await request(
                remote,
                path + "/reviews",
                method="POST",
                json={
                    "commit_id": payload.commit_id,
                    "body": payload.body,
                    "event": payload.event,
                    "comments": [comment.model_dump() for comment in payload.comments],
                },
            )
            yield frame(
                "submitted",
                data={"id": data["id"], "state": data["state"], "html_url": data.get("html_url")},
            )
    except (HTTPException, httpx.HTTPError) as exc:
        message = (
            str(exc.detail)
            if isinstance(exc, HTTPException)
            else (
                "Submission status is unknown. Check GitHub before retrying to avoid a duplicate review."
                if posting
                else "Could not connect to GitHub. Your draft is kept."
            )
        )
        yield frame("error", message=message)


async def check_progress(account, owner, repo, number, check_id):
    """Stream check status/steps, then bounded job logs without forwarding credentials."""

    root = f"/repos/{owner}/{repo}"
    try:
        async with client(account) as remote:
            pr = await request(remote, f"{root}/pulls/{number}")
            for _ in range(120):
                check = await request(remote, f"{root}/check-runs/{check_id}")
                if check.get("head_sha") != pr["head"]["sha"]:
                    yield frame(
                        "error", message="This check belongs to an older commit. Refresh the PR."
                    )
                    return
                yield frame("check", data=check)
                match = re.fullmatch(
                    rf"https://github\.com/{re.escape(owner)}/{re.escape(repo)}/actions/runs/\d+/job/(\d+)(?:\?.*)?",
                    check.get("details_url") or "",
                )
                if not match:
                    yield frame(
                        "notice",
                        message="This provider does not expose GitHub Actions logs. Check output is shown below.",
                    )
                    yield frame("complete")
                    return
                job_id = match.group(1)
                job = await request(remote, f"{root}/actions/jobs/{job_id}")
                yield frame("job", data=job)
                if job.get("status") == "completed":
                    response = await remote.get(f"{root}/actions/jobs/{job_id}/logs")
                    if response.status_code != 302:
                        yield frame(
                            "notice",
                            message="Job logs are unavailable or require Actions read access.",
                        )
                        yield frame("complete")
                        return
                    location = response.headers.get("location", "")
                    parsed = urlparse(location)
                    hostname = parsed.hostname or ""
                    allowed = (
                        hostname.endswith(".blob.core.windows.net")
                        or hostname.endswith(".githubusercontent.com")
                        or hostname == "github.com"
                    )
                    if (
                        parsed.scheme != "https"
                        or not allowed
                        or parsed.username
                        or parsed.password
                    ):
                        yield frame(
                            "error", message="GitHub returned an unsupported log download location."
                        )
                        return
                    # Signed download URL uses its own authorization; never send the GitHub token.
                    async with httpx.AsyncClient(
                        trust_env=False, follow_redirects=False, timeout=45
                    ) as download:
                        async with download.stream("GET", location) as logs:
                            logs.raise_for_status()
                            count = 0
                            async for chunk in logs.aiter_text():
                                remaining = 2_000_000 - count
                                if remaining <= 0:
                                    yield frame("notice", message="Showing the first 2 MB of logs.")
                                    break
                                yield frame("log", data=chunk[:remaining])
                                count += len(chunk)
                    yield frame("complete")
                    return
                yield frame(
                    "notice",
                    message="Following live job steps. Full logs will appear when GitHub publishes them after completion.",
                )
                await asyncio.sleep(5)
            yield frame(
                "notice",
                message="Live monitoring paused after 10 minutes. Reopen this check to continue.",
            )
            yield frame("complete")
    except (HTTPException, httpx.HTTPError) as exc:
        yield frame(
            "error",
            message=(
                str(exc.detail)
                if isinstance(exc, HTTPException)
                else "Could not retrieve check logs from GitHub."
            ),
        )
