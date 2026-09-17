"""Endpoint-scoped model downloads; never installs or controls an Ollama server."""

from __future__ import annotations

import asyncio
import contextlib
import re
from uuid import uuid4

import httpx


def validate_model(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", value):
        raise ValueError("Enter a model name such as qwen3:4b.")
    return value


def endpoint_error(exc: Exception, action="connect") -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return "Access denied. Check this server's API key and permissions."
        if code == 404:
            return (
                "Model not found on this server."
                if action == "remove"
                else "Ollama API not found. Check the server URL."
            )
        if code in (405, 501):
            return "This server does not allow model downloads or removal."
        return f"Ollama returned an error ({code}). Try again."
    if isinstance(exc, httpx.TimeoutException):
        return "The server took too long to respond. Check the connection and retry."
    if isinstance(exc, httpx.ConnectError):
        return "Cannot reach this server. Check the address and that Ollama is running."
    return "The server returned an unexpected response. Check that the URL points to Ollama."


class OllamaPulls:
    def __init__(self):
        self.jobs = {}
        self.tasks = {}
        self.closed = False

    def status(self, instance, endpoint):
        return dict(
            self.jobs.get(
                (instance, endpoint),
                {
                    "phase": "idle",
                    "model": "",
                    "detail": "",
                    "completed": 0,
                    "total": 0,
                },
            )
        )

    async def start(self, instance, provider, model):
        model = validate_model(model)
        key = (instance, provider.base_url)
        if self.closed:
            raise ValueError("Sentinel is shutting down.")
        if key in self.tasks and not self.tasks[key].done():
            raise ValueError(
                "A model is already downloading on this server. Wait for it to finish."
            )
        job = {
            "id": str(uuid4()),
            "phase": "running",
            "model": model,
            "detail": "Preparing download",
            "completed": 0,
            "total": 0,
        }
        self.jobs[key] = job
        self.tasks[key] = asyncio.create_task(self._pull(provider, model, job))
        return dict(job)

    async def _pull(self, provider, model, job):
        layers = {}
        try:
            async with (
                asyncio.timeout(7200),
                contextlib.aclosing(provider.pull_model(model)) as events,
            ):
                async for event in events:
                    if event.get("error"):
                        error = str(event["error"]).lower()
                        job.update(
                            phase="failed",
                            detail=(
                                "Model not found. Check its name and tag."
                                if any(s in error for s in ("not found", "does not exist"))
                                else "The server could not download this model. Check its network access and available disk space."
                            ),
                        )
                        return
                    status = str(event.get("status", ""))
                    if status == "success":
                        job.update(
                            phase="succeeded",
                            detail="Download complete",
                            completed=job["total"],
                        )
                        return
                    digest = event.get("digest")
                    if digest and event.get("total") is not None:
                        total = max(0, int(event["total"]))
                        complete = min(total, max(0, int(event.get("completed", 0))))
                        layers[digest] = (complete, total)
                        job.update(
                            completed=sum(v[0] for v in layers.values()),
                            total=sum(v[1] for v in layers.values()),
                        )
                    job["detail"] = (
                        "Verifying download"
                        if "verif" in status
                        else (
                            "Finishing download"
                            if "manifest" in status and "writing" in status
                            else "Downloading" if layers else "Preparing download"
                        )
                    )
                job.update(
                    phase="failed",
                    detail="The download connection closed early. Retry to resume.",
                )
        except asyncio.CancelledError:
            job.update(phase="failed", detail="Download interrupted. Retry to resume.")
            raise
        except TimeoutError:
            job.update(phase="failed", detail="The download timed out. Retry to resume.")
        except Exception as exc:
            job.update(phase="failed", detail=endpoint_error(exc, "download"))

    async def close(self):
        self.closed = True
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        for task in self.tasks.values():
            with contextlib.suppress(asyncio.CancelledError):
                await task
