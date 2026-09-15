"""Exercise the real backend terminal manager against the desktop/native bridge."""

import asyncio
import json
import os
from uuid import UUID, uuid4
from pathlib import Path
from tests.test_terminal_view import Viewer as PaneViewer

from app.config import settings
from app.services.runtime.container_transport import ContainerTransport
from app.services.runtime.terminal_manager import RuntimeTerminalManager
from app.services.runtime.workspace import WorkspaceLocation


class Viewer(PaneViewer):
    async def receive(self):
        return await self.messages.get()

    async def input(self, pane, text):
        import base64

        await self.send(type="input", pane_id=pane, data=base64.b64encode(text.encode()).decode())


async def main():
    settings.workspace_runtime_socket = os.environ["TEST_RUNTIME_SOCKET"]
    settings.sentinel_desktop_token = os.environ["TEST_RUNTIME_TOKEN"]
    transport = ContainerTransport(
        UUID(os.environ["TEST_WORKSPACE"]), os.environ["TEST_PROJECT"], []
    )
    terminal = RuntimeTerminalManager(
        transport,
        workspace_location=WorkspaceLocation(os.environ["TEST_PROJECT"], "/var/lib/sentinel"),
    )
    session = str(uuid4())
    tasks = []
    from app.services.runtime.panes import TmuxPanes

    pane_updates = asyncio.Event()

    async def publish_panes(panes):
        assert panes
        pane_updates.set()

    try:
        bridge = TmuxPanes(terminal)
        name = 'API\'s "tests" 日本語'
        created = await bridge.create_window(session, name)
        title = 'Pane "one" café'
        await bridge.rename_pane(session, created["pane_id"], title)
        tree = await bridge.tree(session)
        assert tree[0]["name"] == name, tree
        assert tree[0]["panes"][0]["title"] == title, tree
        # tmux escapes backslashes when storing display names. The API must
        # preserve that display value without applying a second escaping layer.
        await bridge.rename_pane(session, created["pane_id"], r"path\name")
        displayed = await bridge.command(
            session, ["display-message", "-p", "-t", created["pane_id"], "#{pane_title}"]
        )
        assert (await bridge.tree(session))[0]["panes"][0]["title"] == displayed.rstrip("\n")
        split = await bridge.split(session, created["pane_id"], "horizontal", "second pane")
        await bridge.close_pane(session, split["pane_id"])
        extra = await bridge.create_window(session, "another window")
        await bridge.close_window(session, extra["window_id"])
        pane_id = created["pane_id"]
        # History created before any UI attaches must appear once, in native scrollback.
        result = await bridge.execute(
            session,
            "for i in $(seq 1 200); do printf 'HISTORY_%03d\\n' \"$i\"; done",
            pane_id=pane_id,
            timeout=15,
        )
        assert result["exit_status"] == 0, result
        first = Viewer()
        tasks.append(
            asyncio.create_task(terminal.attach_ws(session, first, on_panes=publish_panes))
        )
        await first.see(lambda: b"HISTORY_200" in first.output(), tasks[-1])
        await first.send(type="resize", window_id=created["window_id"], cols=100, rows=30)
        await first.input(pane_id, "printf 'BRIDGE_%s\\n' READY\r")
        await first.see(lambda: b"BRIDGE_READY" in first.output(), tasks[-1])
        await asyncio.wait_for(pane_updates.wait(), 10)
        await first.close()
        await asyncio.wait_for(tasks[-1], 10)
        second = Viewer()
        tasks.append(
            asyncio.create_task(terminal.attach_ws(session, second, on_panes=publish_panes))
        )
        await second.see(lambda: b"BRIDGE_READY" in second.output(), tasks[-1])
        assert second.output().count(b"HISTORY_001") == 1
        await second.input(pane_id, "printf 'STILL_%s\\n' CONNECTED\r")
        await second.see(lambda: b"STILL_CONNECTED" in second.output(), tasks[-1])
        await second.send(type="split", pane_id=pane_id, direction="horizontal")
        await second.see(
            lambda: len([e for e in second.events if e["type"] == "pane_snapshot"]) == 2, tasks[-1]
        )
        other = [
            e["pane"]["pane_id"]
            for e in second.events
            if e["type"] == "pane_snapshot" and e["pane"]["pane_id"] != pane_id
        ][0]
        await second.input(other, "printf 'OTHER_%s\\n' PANE\r")
        await second.see(lambda: b"OTHER_PANE" in second.output(other), tasks[-1])
        assert b"OTHER_PANE" not in second.output(pane_id)
        for cols, rows in [(120, 40), (80, 24), (110, 35)]:
            await second.send(type="resize", window_id=created["window_id"], cols=cols, rows=rows)
            await second.see(
                lambda: [e for e in second.events if e["type"] == "terminal_layout"][-1]["windows"][
                    0
                ]["width"]
                == cols,
                tasks[-1],
            )
        await second.close()
        await asyncio.wait_for(tasks[-1], 10)
        if os.environ.get("TEST_TERMINAL_EVENTS"):
            Path(os.environ["TEST_TERMINAL_EVENTS"]).write_text(
                json.dumps([first.events, second.events])
            )
        print(
            "PASS: backend → desktop → native container → tmux control mode; input, separate panes, snapshots, resize and reconnect",
            flush=True,
        )
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await terminal.close()


asyncio.run(main())
