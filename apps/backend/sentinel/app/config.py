import json
from pathlib import Path
from uuid import UUID

from sqlalchemy.engine import URL

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from sentral.llm.ids import ProviderChoice
from sentral.llm.tier_defaults import TierDefaults
from app.services.onboarding.onboarding_defaults import DEFAULT_SYSTEM_PROMPT


class Settings(TierDefaults, BaseSettings):
    sentinel_desktop_token: str = Field(min_length=1)
    workspace_runtime_socket: str | None = Field(
        default=None, validation_alias="SENTINEL_WORKSPACE_RUNTIME_SOCKET"
    )
    app_name: str = "Sentinel API"
    app_env: str = "desktop"
    storage_root: Path = Field(
        validation_alias="SENTINEL_STORAGE_ROOT",
        default_factory=lambda: Path.home() / "Library/Application Support/Sentinel/data",
    )
    data_encryption_key: str = Field(min_length=1)

    # LLM provider credentials live in the per-instance `system_settings` DB
    # table and are populated by SettingsService.build_instance_settings at
    # request time. Env vars are NOT a supported source for these — the
    # validation_alias here points at a sentinel name that no real env var
    # uses, which disables env-var loading for the field while keeping the
    # attribute available for DB hydration via setattr/model_copy.
    anthropic_oauth_token: str | None = Field(
        default=None, validation_alias="_db_only_anthropic_oauth_token"
    )
    anthropic_api_key: str | None = Field(
        default=None, validation_alias="_db_only_anthropic_api_key"
    )
    openai_oauth_token: str | None = Field(
        default=None, validation_alias="_db_only_openai_oauth_token"
    )
    openai_api_key: str | None = Field(default=None, validation_alias="_db_only_openai_api_key")
    openai_base_url: str = "https://api.openai.com/v1"
    gemini_api_key: str | None = Field(default=None, validation_alias="_db_only_gemini_api_key")
    gemini_oauth_credentials: str | None = Field(
        default=None, validation_alias="_db_only_gemini_oauth_credentials"
    )
    primary_provider: ProviderChoice = ProviderChoice.ANTHROPIC
    embedding_api_key: str | None = Field(
        default=None, validation_alias="_db_only_embedding_api_key"
    )
    embedding_model: str = "text-embedding-3-small"
    embedding_base_url: str = "https://api.openai.com/v1"
    memory_embedding_backfill_on_start: bool = True
    memory_embedding_backfill_batch_size: int = 100
    memory_embedding_backfill_max_rows: int = 0
    default_model: str = "claude-opus-5"

    default_system_prompt: str = DEFAULT_SYSTEM_PROMPT
    agent_loop_timeout: float = 1080.0
    tool_image_reinjection_enabled: bool = True
    tool_image_reinjection_max_images: int = 2
    tool_image_reinjection_max_bytes_per_image: int = 2_000_000
    tool_image_reinjection_max_total_bytes: int = 4_000_000
    llm_max_retries: int = 3
    llm_timeout_seconds: int = 60
    chat_default_iterations: int = 0
    chat_max_iterations: int = 100
    context_token_budget: int = 200_000
    stored_tool_result_max_chars: int = 4_000
    stored_tool_call_args_max_chars: int = 1_200
    session_auto_rename_enabled: bool = True
    session_auto_rename_every_messages: int = 10
    session_auto_rename_context_messages: int = 24
    session_auto_rename_model_tier: str = "fast"
    # --- Telegram ---
    telegram_bot_token: str | None = None
    telegram_owner_user_id: str | None = None
    telegram_owner_chat_id: str | None = None
    telegram_owner_telegram_user_id: str | None = None
    telegram_enabled: bool = False

    model_config = SettingsConfigDict(
        extra="ignore",
    )

    def database_path(self, database_name: str) -> Path:
        identifier = str(UUID(database_name))
        if identifier != database_name:
            raise ValueError("Instance database identifier must be a canonical UUID")
        return (
            self.storage_root.expanduser().resolve() / "instances" / identifier / "instance.sqlite"
        )

    def database_url(self, database_name: str) -> str:
        return URL.create(
            "sqlite+aiosqlite", database=str(self.database_path(database_name))
        ).render_as_string(hide_password=False)

    @property
    def manager_database_url(self) -> str:
        path = self.storage_root.expanduser().resolve() / "app.sqlite"
        return URL.create("sqlite+aiosqlite", database=str(path)).render_as_string(
            hide_password=False
        )


settings = Settings()


# ── app version / build identity ──
# The released number lives in the root VERSION file (propagated everywhere else
# by scripts/sync-version.sh). Production payloads also ship a manifest.json at
# the payload root carrying version + channel + the commit the payload was built
# from; when present it is authoritative. Running from source (dev) there is no
# manifest, so the version falls back to VERSION and there is no release
# commit/channel.
def _walk_up_for_marker(name: str) -> str | None:
    cursor = Path(__file__).resolve().parent
    for _ in range(8):
        candidate = cursor / name
        if candidate.is_file():
            value = candidate.read_text(encoding="utf-8").strip()
            return value or None
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    return None


def _read_manifest() -> dict:
    raw = _walk_up_for_marker("manifest.json")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def app_version() -> str:
    return _read_manifest().get("version") or _walk_up_for_marker("VERSION") or "0.0.0"


def build_identity() -> dict[str, str | None]:
    manifest = _read_manifest()
    return {
        "version": manifest.get("version") or _walk_up_for_marker("VERSION"),
        "commit": manifest.get("commit"),
        "channel": manifest.get("channel"),
    }


CHAT_MAX_ITERATIONS = max(1, int(settings.chat_max_iterations))
CHAT_DEFAULT_ITERATIONS = max(
    0,
    min(int(settings.chat_default_iterations), CHAT_MAX_ITERATIONS),
)
