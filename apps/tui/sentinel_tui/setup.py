"""Terminal onboarding using Sentinel's shared provider and tier defaults."""

import os
from dataclasses import dataclass, field

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Button, Collapsible, Footer, Input, Label, Select, Static

from sentinel_tui.credentials import import_login
from sentral import GenerationConfig
from sentral.llm.tier_defaults import TIER_LABELS, TierDefaults

KEY_NAMES = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


@dataclass(frozen=True)
class Configuration:
    provider: str
    generation: GenerationConfig
    api_key: str = field(repr=False)
    base_url: str | None = None
    auth_method: str = "api_key"

    @property
    def model(self):
        return self.generation.model


class Setup(App[Configuration | None]):
    TITLE = "Sentral"
    CSS = """
    Screen {align: center middle;}
    #setup {width: 64; max-width: 100%; height: auto; max-height: 100%; padding: 1 2;}
    #title {text-style: bold; margin-bottom: 1;}
    #hint {color: $text-muted; height: auto;}
    #error {color: $error; height: auto;}
    #start {margin-top: 1; width: 100%;}
    Collapsible {padding: 0; border: none;}
    """
    BINDINGS = [("ctrl+q", "quit", "Quit")]

    def __init__(self, provider=None, model=None, base_url=None):
        super().__init__()
        self.initial_provider = provider or "openai"
        self.initial_model = model or ""
        self.initial_url = base_url or ""
        self.defaults = TierDefaults()

    def tier_options(self, provider):
        return [
            (f"{label} · {self.defaults.generation(provider, tier).model}", tier.value)
            for tier, (label, _) in TIER_LABELS.items()
        ]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="setup"):
            yield Static("Welcome to Sentral", id="title")
            yield Select(
                [("OpenAI", "openai"), ("Anthropic", "anthropic"), ("Google Gemini", "gemini")],
                value=self.initial_provider,
                allow_blank=False,
                id="provider",
            )
            yield Select(
                [("API key", "api_key"), ("Use existing CLI login (OAuth)", "oauth")],
                value="api_key",
                allow_blank=False,
                id="auth",
            )
            yield Input(password=True, placeholder="Paste API key", id="key")
            yield Static("", id="hint")
            yield Label("Effort")
            yield Select(
                self.tier_options(self.initial_provider),
                value="normal",
                allow_blank=False,
                id="tier",
            )
            with Collapsible(
                title="Advanced", collapsed=not bool(self.initial_model or self.initial_url)
            ):
                yield Input(
                    value=self.initial_model, placeholder="Custom model (optional)", id="model"
                )
                yield Input(
                    value=self.initial_url,
                    placeholder="OpenAI-compatible endpoint (optional)",
                    id="url",
                )
            yield Static("", id="error")
            yield Button("Start chatting", variant="primary", id="start")
        yield Footer()

    def on_mount(self):
        self.update_provider(self.initial_provider)

    def update_provider(self, provider):
        key_name = KEY_NAMES[provider]
        found = bool(os.environ.get(key_name))
        self.query_one("#key", Input).placeholder = (
            "API key found · paste to replace" if found else "Paste API key"
        )
        self.query_one("#hint", Static).update(
            "Key available from environment"
            if found
            else "Your key stays in memory for this session."
        )
        oauth = self.query_one("#auth", Select).value == "oauth"
        self.query_one("#url", Input).disabled = provider != "openai" or oauth
        if oauth:
            self.query_one("#hint", Static).update(
                "Uses your Codex, Claude Code, or Antigravity login."
            )
        tier = self.query_one("#tier", Select)
        selected = tier.value
        tier.set_options(self.tier_options(provider))
        tier.value = selected

    def on_select_changed(self, event: Select.Changed):
        if event.select.id == "auth":
            oauth = event.value == "oauth"
            self.query_one("#key", Input).display = not oauth
            self.query_one("#key", Input).value = ""
            self.query_one("#url", Input).disabled = (
                oauth or self.query_one("#provider", Select).value != "openai"
            )
            self.query_one("#hint", Static).update(
                "Uses your Codex, Claude Code, or Antigravity login."
                if oauth
                else "Your key stays in memory for this session."
            )
        if event.select.id == "provider":
            self.query_one("#key", Input).value = ""
            self.update_provider(str(event.value))

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id != "start":
            return
        provider = str(self.query_one("#provider", Select).value)
        key = self.query_one("#key", Input).value.strip() or os.environ.get(KEY_NAMES[provider], "")
        auth = str(self.query_one("#auth", Select).value)
        if auth == "oauth":
            event.button.disabled = True
            self.query_one("#error", Static).update("Reading CLI login…")
            try:
                key = await import_login(provider)
            except (ValueError, OSError, TimeoutError) as exc:
                self.query_one("#error", Static).update(
                    str(exc) or "Could not read the CLI login. Try again."
                )
                return
            finally:
                event.button.disabled = False
        if not key:
            self.query_one("#error", Static).update("Paste an API key to continue.")
            self.query_one("#key", Input).focus()
            return
        generation = self.defaults.generation(
            "codex" if provider == "openai" and auth == "oauth" else provider,
            str(self.query_one("#tier", Select).value),
        )
        custom = self.query_one("#model", Input).value.strip()
        if custom:
            generation = GenerationConfig(model=custom)
        url = (
            self.query_one("#url", Input).value.strip()
            if provider == "openai" and auth == "api_key"
            else ""
        )
        self.exit(Configuration(provider, generation, key, url or None, auth))
