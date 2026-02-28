from pydantic import BaseModel, Field, field_validator


class TokenExchangeRequest(BaseModel):
    araios_token: str = Field(min_length=1)

    @field_validator("araios_token")
    @classmethod
    def _normalize_araios_token(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("araios_token must not be empty")
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


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
