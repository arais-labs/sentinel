from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, model_validator


class MCPConnection(BaseModel):
    transport: Literal["auto", "stdio", "streamable-http", "sse"] = "auto"
    url: str = ""
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    oauth: dict = Field(default_factory=dict, exclude=False)
    timeout_seconds: int = Field(default=60, ge=5, le=300)

    @model_validator(mode="after")
    def validate_connection(self):
        if self.transport == "stdio":
            if not self.command.strip():
                raise ValueError("A command is required for a local server")
            if self.url or self.headers:
                raise ValueError("Local servers use command arguments and environment variables")
        else:
            parts = urlsplit(self.url)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                raise ValueError("Enter an HTTP or HTTPS server URL")
            if parts.username or parts.password or parts.fragment:
                raise ValueError(
                    "Use headers for authentication; URLs cannot contain credentials or fragments"
                )
            if self.command or self.args or self.env:
                raise ValueError("Remote servers use URLs and headers")
        return self


class MCPServerCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    connection: MCPConnection


class MCPServerToggle(BaseModel):
    enabled: bool | None = None
    always_load: bool | None = None


class MCPConnectionEdit(BaseModel):
    transport: Literal["auto", "stdio", "streamable-http", "sse"] | None = None
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    # Null preserves a saved value; omitting the entire map preserves all keys.
    env: dict[str, str | None] | None = None
    headers: dict[str, str | None] | None = None
    timeout_seconds: int | None = Field(default=None, ge=5, le=300)


class MCPServerEdit(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    connection: MCPConnectionEdit
