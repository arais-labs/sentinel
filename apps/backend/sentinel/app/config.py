from urllib.parse import quote

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal

from app.services.llm.ids import ProviderChoice
from app.services.onboarding.onboarding_defaults import DEFAULT_SYSTEM_PROMPT


class Settings(BaseSettings):
    app_name: str = "Sentinel API"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    database_host: str = "localhost"
    database_port: int = 5432
    database_user: str = "sentinel"
    database_password: str = "sentinel"
    database_maintenance_name: str = "postgres"
    database_manager_name: str = "sentinel_manager"
    jwt_secret_key: str = Field(min_length=1)
    data_encryption_key: str = Field(min_length=1)
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_seconds: int = 604800
    auth_cookie_secure: bool = False
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    # Required in server mode (enforced at startup); optional in desktop mode.
    sentinel_auth_username: str = ""
    sentinel_auth_password: str = ""
    dev_user_id: str = "dev-admin"

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
    default_model: str = "claude-sonnet-4-6"

    # --- Tier: Fast ---
    tier_fast_anthropic_model: str = "claude-haiku-4-5-20251001"
    tier_fast_openai_model: str = "gpt-4o-mini"
    tier_fast_codex_model: str = "gpt-5.4-mini"
    tier_fast_gemini_model: str = "gemini-3-flash-preview"
    tier_fast_max_tokens: int = 4096
    tier_fast_temperature: float = 0.3
    tier_fast_anthropic_thinking_budget: int = 0
    tier_fast_openai_reasoning_effort: str = ""
    tier_fast_gemini_thinking_budget: int = 0

    # --- Tier: Normal ---
    tier_normal_anthropic_model: str = "claude-sonnet-4-6"
    tier_normal_openai_model: str = "gpt-4o"
    tier_normal_codex_model: str = "gpt-5.3-codex"
    tier_normal_gemini_model: str = "gemini-3-flash-preview"
    tier_normal_max_tokens: int = 8192
    tier_normal_temperature: float = 0.7
    tier_normal_anthropic_thinking_budget: int = 5000
    tier_normal_openai_reasoning_effort: str = "medium"
    tier_normal_gemini_thinking_budget: int = 0

    # --- Tier: Hard ---
    tier_hard_anthropic_model: str = "claude-opus-4-6"
    tier_hard_openai_model: str = "o3"
    tier_hard_codex_model: str = "gpt-5.5"
    tier_hard_gemini_model: str = "gemini-3.1-pro-preview"
    tier_hard_max_tokens: int = 40000
    tier_hard_temperature: float = 0.7
    tier_hard_anthropic_thinking_budget: int = 32000
    tier_hard_openai_reasoning_effort: str = "high"
    tier_hard_gemini_thinking_budget: int = 32000
    default_system_prompt: str = DEFAULT_SYSTEM_PROMPT
    agent_loop_timeout: float = 1080.0
    tool_image_reinjection_enabled: bool = True
    tool_image_reinjection_max_images: int = 2
    tool_image_reinjection_max_bytes_per_image: int = 2_000_000
    tool_image_reinjection_max_total_bytes: int = 4_000_000
    llm_max_retries: int = 3
    llm_timeout_seconds: int = 60
    chat_default_iterations: int = 25
    chat_max_iterations: int = 100
    context_token_budget: int = 200_000
    stored_tool_result_max_chars: int = 4_000
    stored_tool_call_args_max_chars: int = 1_200
    runtime_lima_yaml: str = ""
    runtime_docker_base_image: str = "debian:trixie"
    runtime_docker_ssh_host: str = "host.docker.internal"
    session_auto_rename_enabled: bool = True
    session_auto_rename_every_messages: int = 10
    session_auto_rename_context_messages: int = 24
    session_auto_rename_model_tier: str = "fast"
    # --- Telegram ---
    telegram_bot_token: str | None = None
    telegram_owner_user_id: str | None = None
    telegram_owner_chat_id: str | None = None
    telegram_owner_telegram_user_id: str | None = None
    telegram_pairing_code_hash: str | None = None
    telegram_pairing_code_expires_at: str | None = None
    telegram_enabled: bool = False

    model_config = SettingsConfigDict(
        extra="ignore",
    )

    def database_url(self, database_name: str) -> str:
        user = quote(self.database_user, safe="")
        password = quote(self.database_password, safe="")
        host = self.database_host
        port = int(self.database_port)
        database = quote(database_name, safe="")
        return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"

    @property
    def manager_database_url(self) -> str:
        return self.database_url(self.database_manager_name)


settings = Settings()


def is_desktop_app() -> bool:
    return (settings.app_env or "").strip().lower() == "desktop"


CHAT_MAX_ITERATIONS = max(1, int(settings.chat_max_iterations))
CHAT_DEFAULT_ITERATIONS = max(
    1,
    min(int(settings.chat_default_iterations), CHAT_MAX_ITERATIONS),
)
