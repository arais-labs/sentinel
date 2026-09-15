"""A terminal view over Sentral; no application backend is started."""

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Markdown, Static

from sentral import (
    AgentRuntimeEngine,
    ConversationItem,
    GenerationConfig,
    RunTurnRequest,
    TextBlock,
)
from sentral.llm.http_pool import close_provider_http_pool
from sentral.llm.providers.codex import close_codex_connections
from sentinel_tui.tools import Tools


class Approval(ModalScreen[bool]):
    DEFAULT_CSS = """
    Approval {align: center middle; background: $background 70%;}
    Approval > VerticalScroll {width: 80%; height: 60%; border: round $warning; padding: 1 2; background: $surface;}
    Approval Horizontal {height: 3;}
    """

    def __init__(self, name, payload):
        super().__init__()
        self.tool_name, self.payload = name, payload

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(f"Allow {self.tool_name}?", markup=False)
            yield Static(json.dumps(self.payload, indent=2), markup=False)
            with Horizontal():
                yield Button("Deny", id="deny")
                yield Button("Allow once", id="allow", variant="warning")

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "allow")

    def key_escape(self):
        self.dismiss(False)


class Chat(App):
    TITLE = "Sentral · local chat"
    CSS = """
    #messages {height: 1fr; padding: 1 2;}
    Markdown {margin: 0 0 1 0;}
    #status {height: 1; color: $text-muted;}
    """
    BINDINGS = [("ctrl+c", "cancel", "Stop turn"), ("ctrl+q", "quit", "Quit")]

    def __init__(self, provider, model, *, generation=None):
        super().__init__()
        self.model = model
        self.generation = generation or GenerationConfig(model=model)
        self.tools = Tools(self.approve)
        self.engine = AgentRuntimeEngine(provider=provider, tool_registry=self.tools)
        self.history = []
        self.pending = []
        self.turn = None
        self.approval_lock = asyncio.Lock()

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="messages")
        yield Static("Commands run on this machine. /new clears chat · /help", id="status")
        yield Input(placeholder="Message or steer the running turn…", id="prompt")
        yield Footer()

    def on_mount(self):
        self.query_one("#prompt", Input).focus()

    async def approve(self, name, payload):
        async with self.approval_lock:
            future = asyncio.get_running_loop().create_future()

            def result(value):
                if not future.done():
                    future.set_result(bool(value))

            self.push_screen(Approval(name, payload), result)
            try:
                return await future
            finally:
                if isinstance(self.screen, Approval):
                    self.pop_screen()

    async def show(self, text):
        widget = Markdown(text)
        await self.query_one("#messages").mount(widget)
        self.query_one("#messages").scroll_end(animate=False)
        return widget

    async def on_input_submitted(self, event: Input.Submitted):
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text == "/help":
            await self.show(
                "**Commands:** /new clears an idle chat; Ctrl+C stops generation; Ctrl+Q exits and terminates owned processes. Messages during a turn steer it. Tool execution requires approval. History is held only in memory."
            )
            return
        if text == "/new":
            if self.turn and not self.turn.done():
                await self.show("Stop the current turn before starting a new chat.")
            else:
                self.history.clear()
                await self.query_one("#messages").remove_children()
            return
        item = ConversationItem(uuid4().hex, "user", [TextBlock(text=text)])
        await self.show("**You:** " + text)
        if self.turn and not self.turn.done():
            self.pending.append(item)
        else:
            self.turn = asyncio.create_task(self.run_turn(item))

    def interjections(self):
        items, self.pending = self.pending, []
        return items

    async def run_turn(self, item):
        response = None
        text = ""
        self.query_one("#status", Static).update(
            "Running · enter a message to steer · Ctrl+C stops"
        )

        async def checkpoint(history):
            self.history = list(history)

        async def sink(event):
            nonlocal response, text
            if event.type == "text_delta" and event.delta:
                if response is None:
                    response = await self.show("")
                text += event.delta
                await response.update(text)
                self.query_one("#messages").scroll_end(animate=False)
            elif event.type == "toolcall_end" and event.tool_call:
                await self.show(
                    "**Tool:** `"
                    + event.tool_call.name
                    + "`\n```json\n"
                    + json.dumps(event.tool_call.arguments, indent=2)
                    + "\n```"
                )
                response, text = None, ""
            elif event.type == "tool_result" and event.tool_result:
                await self.show("```text\n" + event.tool_result.content + "\n```")

        try:
            result = await self.engine.run_turn(
                RunTurnRequest(
                    history=self.history,
                    new_items=[item],
                    config=replace(
                        self.generation,
                        system_prompt="You are a terminal assistant. Tools operate on the current local machine. Ask before destructive actions.",
                    ),
                    interjection_source=self.interjections,
                ),
                sink=sink,
                checkpoint=checkpoint,
            )
            self.history = result.history
            if result.error:
                await self.show("**Error:** " + result.error)
        except asyncio.CancelledError:
            await self.show(
                "Turn stopped. Started commands may still run; use host_runtime list to inspect them."
            )
        except Exception as exc:
            await self.show("**Error:** " + str(exc))
        finally:
            # Preserve steering not consumed before completion/cancellation.
            self.history.extend(self.interjections())
            self.query_one("#status", Static).update("Ready · /help")

    def action_cancel(self):
        if self.turn and not self.turn.done():
            self.turn.cancel()

    async def on_unmount(self):
        if self.turn and not self.turn.done():
            self.turn.cancel()
            await asyncio.gather(self.turn, return_exceptions=True)
        await self.tools.close()
        await close_codex_connections()
        await close_provider_http_pool()
