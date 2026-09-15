"""Remote update transaction, coordinated by the newly verified helper.

The coordinator holds OS locks, not a stale PID file. Only it changes the active
manifest; service ownership is handed directly to its child at activation.
"""

import app.services.runtime.remote_mac as remote_mac

import asyncio
import json
from shlex import quote


class UpdateSession:
    def __init__(self, conn, executable, root):
        self.conn, self.executable, self.root = conn, executable, root
        self.process = None
        self.serial = asyncio.Lock()
        self.heartbeat = None
        self.failure = None

    async def __aenter__(self):

        self.process = await self.conn.create_process(
            " ".join(map(quote, [self.executable, "--update-session", self.root])),
            encoding="utf-8",
        )
        try:
            result = await self.receive()
            if result.get("event") != "locked":
                raise remote_mac.RemoteMacError("Another runtime update is in progress")
        except BaseException:
            self.process.close()
            raise
        self.heartbeat = asyncio.create_task(self.keep_alive())
        return self

    async def keep_alive(self):
        try:
            while True:
                await asyncio.sleep(10)
                await self.request("heartbeat")
        except Exception as exc:
            self.failure = exc
            self.process.close()

    async def receive(self):

        async with asyncio.timeout(30):
            line = await self.process.stdout.readline()
        if not line:
            raise remote_mac.RemoteMacError(
                "Update connection lost. Reopen Runtime to recover the update."
            )
        result = json.loads(line)
        if result.get("error"):
            raise remote_mac.RemoteMacError(result["error"])
        return result

    async def request(self, action, **values):
        async with self.serial:
            if self.failure:
                raise self.failure
            self.process.stdin.write(json.dumps({"action": action, **values}) + "\n")
            return await self.receive()

    async def __aexit__(self, *args):
        # EOF releases the update lease, not the separate service ownership lock.
        if self.heartbeat:
            self.heartbeat.cancel()
            await asyncio.gather(self.heartbeat, return_exceptions=True)
        try:
            self.process.stdin.write_eof()
            async with asyncio.timeout(5):
                await self.process.wait_closed()
        except Exception:
            if not args[0]:
                raise
        finally:
            self.process.close()


async def apply_update(
    session,
    target,
    *,
    snapshot,
    stop,
    validate,
    resume,
    approved,
    progress,
    reinstall=False,
    owner_timeout=15,
):
    """Resume from observed manifest/ownership, never replay an uncertain mutation."""

    state = await session.request("inspect")
    current = state["manifest"]
    prior = state.get("journal") or {}
    interrupted = prior.get("phase") not in (None, "complete", "rolled_back")
    if current and current.get("storeVersion", 1) != target["storeVersion"]:
        raise remote_mac.RemoteMacError(
            "This runtime requires a workspace storage migration; update not applied"
        )
    live = not state["owner_free"]
    running = []
    if live:
        # An unavailable/unresponsive socket with a held lock is NOT a dead
        # service. Never take over its disks or kill it on a timeout.
        status = await snapshot(current)
        running = [id for id, value in status.get("states", {}).items() if value == "running"]
        if (
            current
            and current.get("version") == target["version"]
            and (not reinstall or interrupted)
        ):
            # Reconcile activation whose response was lost. Validate in place;
            # don't stop it or repeat workspace startup/provisioning.
            await validate(current)
            await session.request(
                "journal",
                journal={
                    **prior,
                    "phase": "complete",
                    "warning": (
                        "Interrupted update recovered. Retry any stopped workspaces; commands were not replayed."
                        if interrupted
                        else prior.get("warning", "")
                    ),
                },
            )
            return
        if not set(running).issubset(set(approved)):
            raise remote_mac.UpdateApprovalRequired(running)
    previous = prior.get("previous") if interrupted else current
    journal = {
        "phase": "staged",
        "previous": previous,
        "target": target,
        "running": running,
    }

    async def phase(value, **extra):
        journal.update(phase=value, **extra)
        progress(value)
        await session.request("journal", journal=journal)

    async def wait_for_owner_release():
        # Socket EOF can precede the OS releasing the last owner descriptor.
        # Wait for that ownership boundary; never substitute a PID/liveness guess.
        try:
            async with asyncio.timeout(owner_timeout):
                while not (await session.request("inspect"))["owner_free"]:
                    await asyncio.sleep(0.05)
        except TimeoutError as exc:
            raise remote_mac.RemoteMacError(
                "Runtime still owns workspace storage; update not applied"
            ) from exc

    await phase("staged")
    if live:
        await phase("stopping")
        await stop(current, running)
    await wait_for_owner_release()
    await phase("activating")
    try:
        await session.request("activate", manifest=target)
        await phase("initializing")
        await validate(target)
    except Exception as exc:
        # Roll back only before resuming workspaces, and only to a release which
        # understands the ownership handoff and the same storage format.
        state = await session.request("inspect")
        if (
            previous
            and previous.get("updateProtocol") == 1
            and previous.get("storeVersion") == target["storeVersion"]
        ):
            if not state["owner_free"]:
                await stop(target, [])
                await wait_for_owner_release()
            await phase("rolling_back", error=str(exc))
            await session.request("activate", manifest=previous)
            await validate(previous)
            await phase("rolled_back")
            raise remote_mac.RemoteMacError(
                f"Runtime update failed; previous runtime restored: {exc}"
            ) from exc
        await phase("recovery_required", error=str(exc))
        raise remote_mac.RemoteMacError(
            f"Runtime update needs recovery: {exc}. Workspace files are preserved; "
            "reopen Runtime to retry. The previous release cannot be safely auto-started."
        ) from exc
    # Failures after workspace startup begins must not trigger disk rollback or
    # replay setup. Keep the healthy replacement and expose workspace Retry.
    await phase("resuming")
    try:
        if running and not interrupted:
            await resume(running)
    except Exception as exc:
        await phase("complete", warning=f"Runtime updated; retry affected workspaces: {exc}")
        raise remote_mac.RemoteMacError(journal["warning"]) from exc
    await phase(
        "complete",
        warning=(
            "Interrupted update recovered. Retry any stopped workspaces; commands were not replayed."
            if interrupted
            else ""
        ),
    )
