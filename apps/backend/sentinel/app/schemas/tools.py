from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolSummaryResponse(BaseModel):
    name: str
    description: str
    enabled: bool


class ToolDetailResponse(ToolSummaryResponse):
    parameters_schema: dict[str, Any] = Field(default_factory=dict)


class ToolListResponse(BaseModel):
    items: list[ToolSummaryResponse]


class ToolExecuteRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    runtime_context: dict[str, Any] = Field(default_factory=dict)


class ToolExecuteResponse(BaseModel):
    result: Any
    duration_ms: int
