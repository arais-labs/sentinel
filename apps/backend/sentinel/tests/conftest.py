from __future__ import annotations

import os

# Shared pytest defaults so local test runs do not depend on shell-exported env vars.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
os.environ.setdefault("TOOL_FILE_READ_BASE_DIR", "/tmp")
