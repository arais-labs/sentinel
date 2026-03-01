from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)

    @field_validator("username")
    @classmethod
    def _normalize_username(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("username must not be empty")
        return trimmed

    @field_validator("password")
    @classmethod
    def _normalize_password(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("password must not be empty")
        return trimmed


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)

    @field_validator("refresh_token")
    @classmethod
    def _normalize_refresh_token(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("refresh_token must not be empty")
        return trimmed


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=1)

    @field_validator("current_password", "new_password")
    @classmethod
    def _normalize_password_fields(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("password fields must not be empty")
        return trimmed


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class AuthMeResponse(BaseModel):
    sub: str
    role: str
    agent_id: str | None = None
