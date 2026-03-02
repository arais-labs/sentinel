from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal

from app.services.llm.ids import ProviderChoice
from app.services.onboarding_defaults import DEFAULT_SYSTEM_PROMPT


class Settings(BaseSettings):
    app_name: str = "Sentinel API"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    database_url: str = "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel"
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_seconds: int = 604800
    auth_cookie_secure: bool = False
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    sentinel_auth_username: str = "admin"
    sentinel_auth_password: str = "admin"
    araios_url: str | None = None
    dev_user_id: str = "dev-admin"
    anthropic_oauth_token: str | None = None
    anthropic_api_key: str | None = None
    openai_oauth_token: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    gemini_api_key: str | None = None
    primary_provider: ProviderChoice = ProviderChoice.ANTHROPIC
    embedding_api_key: str | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_base_url: str = "https://api.openai.com/v1"
    memory_embedding_backfill_on_start: bool = True
    memory_embedding_backfill_batch_size: int = 100
    memory_embedding_backfill_max_rows: int = 0
    default_model: str = "claude-sonnet-4-6"

    # --- Tier: Fast ---
    tier_fast_anthropic_model: str = "claude-haiku-4-5-20251001"
    tier_fast_openai_model: str = "gpt-4o-mini"
    tier_fast_codex_model: str = "gpt-5.3-codex-spark"
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
    tier_normal_openai_reasoning_effort: str = ""
    tier_normal_gemini_thinking_budget: int = 0

    # --- Tier: Hard ---
    tier_hard_anthropic_model: str = "claude-opus-4-6"
    tier_hard_openai_model: str = "o3"
    tier_hard_codex_model: str = "gpt-5.3-codex"
    tier_hard_gemini_model: str = "gemini-3.1-pro-preview"
    tier_hard_max_tokens: int = 40000
    tier_hard_temperature: float = 0.7
    tier_hard_anthropic_thinking_budget: int = 32000
    tier_hard_openai_reasoning_effort: str = "high"
    tier_hard_gemini_thinking_budget: int = 32000
    default_system_prompt: str = DEFAULT_SYSTEM_PROMPT
    agent_loop_timeout: float = 1080.0
    agent_loop_cooldown: float = 1.5  # seconds between each agent loop iteration
    tool_image_reinjection_enabled: bool = True
    tool_image_reinjection_max_images: int = 2
    tool_image_reinjection_max_bytes_per_image: int = 2_000_000
    tool_image_reinjection_max_total_bytes: int = 4_000_000
    llm_max_retries: int = 3
    llm_timeout_seconds: int = 60
    chat_default_iterations: int = 25
    chat_max_iterations: int = 100
    browser_live_view_enabled: bool = True
    browser_live_public_url: str | None = None
    browser_live_host: str = "127.0.0.1"
    browser_live_port: int = 6080
    browser_live_path: str = "/vnc.html"
    browser_live_view_only: bool = False
    browser_live_autoconnect: bool = True
    browser_live_resize: str = "scale"
    browser_live_probe_timeout_ms: int = 500
    browser_prewarm_on_start: bool = False
    browser_vnc_password: str | None = None
    context_token_budget: int = 200_000
    stored_tool_result_max_chars: int = 4_000
    stored_tool_call_args_max_chars: int = 1_200
    compaction_auto_resume_enabled: bool = True
    git_push_approval_timeout_seconds: int = 600

    # --- Telegram ---
    telegram_bot_token: str | None = None
    telegram_owner_user_id: str | None = None
    telegram_owner_chat_id: str | None = None
    telegram_owner_telegram_user_id: str | None = None
    telegram_pairing_code_hash: str | None = None
    telegram_pairing_code_expires_at: str | None = None
    telegram_enabled: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()

CHAT_MAX_ITERATIONS = max(1, int(settings.chat_max_iterations))
CHAT_DEFAULT_ITERATIONS = max(
    1,
    min(int(settings.chat_default_iterations), CHAT_MAX_ITERATIONS),
)
