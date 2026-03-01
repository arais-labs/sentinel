import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://araios:araios@localhost:5432/araios")
ENV = os.getenv("ENV", "development")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_samesite(name: str, default: str = "lax") -> str:
    raw = os.getenv(name, default).strip().lower()
    if raw in {"lax", "strict", "none"}:
        return raw
    return default

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-me-please-use-a-long-random-secret")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_TTL_SECONDS = int(os.getenv("ACCESS_TOKEN_TTL_SECONDS", "3600"))
REFRESH_TOKEN_TTL_SECONDS = int(os.getenv("REFRESH_TOKEN_TTL_SECONDS", "604800"))
AUTH_COOKIE_SECURE = _env_bool("AUTH_COOKIE_SECURE", False)
AUTH_COOKIE_SAMESITE = _env_samesite("AUTH_COOKIE_SAMESITE", "lax")

ARAIOS_AUTH_USERNAME = os.getenv("ARAIOS_AUTH_USERNAME", "admin")
ARAIOS_AUTH_PASSWORD = os.getenv("ARAIOS_AUTH_PASSWORD", "admin")
