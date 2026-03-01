import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://araios:araios@localhost:5432/araios")
ENV = os.getenv("ENV", "development")

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-me-please-use-a-long-random-secret")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_TTL_SECONDS = int(os.getenv("ACCESS_TOKEN_TTL_SECONDS", "3600"))
REFRESH_TOKEN_TTL_SECONDS = int(os.getenv("REFRESH_TOKEN_TTL_SECONDS", "604800"))

# Bootstraps the first API key if none exists yet.
PLATFORM_BOOTSTRAP_API_KEY = os.getenv("PLATFORM_BOOTSTRAP_API_KEY", "")
PLATFORM_BOOTSTRAP_LABEL = "Bootstrap Admin"
PLATFORM_BOOTSTRAP_ROLE = "admin"
PLATFORM_BOOTSTRAP_SUB = "platform-admin"
PLATFORM_BOOTSTRAP_AGENT_ID = "admin"
