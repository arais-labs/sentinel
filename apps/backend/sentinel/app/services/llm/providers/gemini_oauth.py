"""Refreshable Google OAuth credentials for Gemini CLI / Code Assist access."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict

from app.services.llm.generic.errors import TransientProviderError
from app.services.llm.generic.types import AgentEvent, AgentMessage, AssistantMessage, ReasoningConfig, ToolCallContent, ToolSchema
from app.services.llm.providers.gemini import GeminiProvider, _map_finish_reason

_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 60
_CODE_ASSIST_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_DEFAULT_CODE_ASSIST_BASE_URL = "https://cloudcode-pa.googleapis.com/v1internal"
_CODE_ASSIST_CLIENT_METADATA = {
    "ideType": "IDE_UNSPECIFIED",
    "platform": "PLATFORM_UNSPECIFIED",
    "pluginType": "GEMINI",
}
_CODE_ASSIST_MODEL_RESOLUTIONS: dict[str, str] = {
    "auto": "gemini-3-pro-preview",
    "pro": "gemini-3-pro-preview",
    "flash": "gemini-3-flash-preview",
    "flash-lite": "gemini-2.5-flash-lite",
    "auto-gemini-3": "gemini-3-pro-preview",
    "auto-gemini-2.5": "gemini-2.5-pro",
}
_CODE_ASSIST_MODEL_FALLBACKS: dict[str, tuple[str, ...]] = {
    "gemini-3.1-pro-preview": ("gemini-3-pro-preview", "gemini-3-flash-preview"),
    "gemini-3.1-pro-preview-customtools": (
        "gemini-3.1-pro-preview",
        "gemini-3-pro-preview",
        "gemini-3-flash-preview",
    ),
    "gemini-3-pro-preview": ("gemini-3-flash-preview",),
    "gemini-3-flash-preview": (),
    "gemini-3.1-flash-lite-preview": ("gemini-2.5-flash-lite", "gemini-2.5-flash"),
    "gemini-2.5-pro": ("gemini-2.5-flash",),
    "gemini-2.5-flash": (),
    "gemini-2.5-flash-lite": ("gemini-2.5-flash", "gemini-2.5-pro"),
}
_MODEL_CAPACITY_COOLDOWN_SECONDS = 60


class GeminiCodeAssistHTTPError(RuntimeError):
    """Code Assist HTTP error with a status code for retry/fallback logic."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"Gemini Code Assist http_{status_code}: {detail}")
        self.status_code = status_code


class GeminiOAuthCredentials(BaseModel):
    """Canonical refreshable Google OAuth credential bundle."""

    model_config = ConfigDict(extra="ignore")

    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "Bearer"
    scope: str | None = None
    expiry_date: int | None = None
    client_id: str | None = None
    client_secret: str | None = None
    token_uri: str | None = None
    quota_project_id: str | None = None

    @classmethod
    def parse_input(cls, value: str | dict[str, Any]) -> "GeminiOAuthCredentials":
        if isinstance(value, str):
            try:
                raw = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("Gemini OAuth credentials must be valid JSON.") from exc
        elif isinstance(value, dict):
            raw = value
        else:
            raise ValueError("Gemini OAuth credentials must be JSON.")

        if not isinstance(raw, dict):
            raise ValueError("Gemini OAuth credentials must decode to a JSON object.")

        token = raw.get("token")
        if isinstance(token, dict):
            normalized: dict[str, Any] = {
                "access_token": token.get("accessToken") or token.get("access_token"),
                "refresh_token": token.get("refreshToken") or token.get("refresh_token"),
                "token_type": token.get("tokenType") or token.get("token_type") or "Bearer",
                "scope": token.get("scope"),
                "expiry_date": token.get("expiresAt") or token.get("expiry_date"),
            }
            normalized["client_id"] = raw.get("clientId") or raw.get("client_id")
            normalized["client_secret"] = raw.get("clientSecret") or raw.get("client_secret")
            normalized["token_uri"] = raw.get("tokenUrl") or raw.get("token_uri")
            normalized["quota_project_id"] = (
                raw.get("quotaProjectId")
                or raw.get("quota_project_id")
                or raw.get("projectId")
                or raw.get("project_id")
            )
            raw = normalized
        else:
            raw = {
                "access_token": raw.get("access_token"),
                "refresh_token": raw.get("refresh_token"),
                "token_type": raw.get("token_type") or "Bearer",
                "scope": raw.get("scope"),
                "expiry_date": raw.get("expiry_date"),
                "client_id": raw.get("client_id"),
                "client_secret": raw.get("client_secret"),
                "token_uri": raw.get("token_uri"),
                "quota_project_id": (
                    raw.get("quota_project_id")
                    or raw.get("quotaProjectId")
                    or raw.get("project_id")
                    or raw.get("projectId")
                ),
            }

        creds = cls.model_validate(raw)
        if not (creds.refresh_token or creds.access_token):
            raise ValueError(
                "Gemini OAuth credentials must include at least an access_token or refresh_token."
            )
        if not creds.refresh_token:
            raise ValueError(
                "Gemini OAuth credentials must include refresh_token for long-lived access."
            )
        if not creds.client_id:
            raise ValueError("Gemini OAuth credentials must include client_id.")
        if not creds.client_secret:
            raise ValueError("Gemini OAuth credentials must include client_secret.")
        return creds

    def as_json(self) -> str:
        return self.model_dump_json(exclude_none=True)

    def mask_secret(self) -> str | None:
        value = self.refresh_token or self.access_token
        if not value:
            return None
        if len(value) <= 8:
            return "****"
        return value[:4] + "..." + value[-4:]

    def resolved_client_id(self) -> str:
        if not self.client_id:
            raise RuntimeError("Gemini OAuth credentials are missing client_id.")
        return self.client_id

    def resolved_client_secret(self) -> str:
        if not self.client_secret:
            raise RuntimeError("Gemini OAuth credentials are missing client_secret.")
        return self.client_secret

    def resolved_token_uri(self) -> str:
        return self.token_uri or _GOOGLE_TOKEN_URL

    def scope_values(self) -> set[str]:
        raw_scope = (self.scope or "").strip()
        if not raw_scope:
            return set()
        return {item.strip() for item in raw_scope.split() if item.strip()}

    def has_required_code_assist_scope(self) -> bool:
        scopes = self.scope_values()
        return not scopes or _CODE_ASSIST_SCOPE in scopes


class GeminiOAuthProvider(GeminiProvider):
    """Gemini provider using refreshable Google OAuth credentials against Code Assist."""

    def __init__(
        self,
        credentials: GeminiOAuthCredentials | str | dict[str, Any],
        *,
        base_url: str = _DEFAULT_CODE_ASSIST_BASE_URL,
        client_factory=None,
    ) -> None:
        parsed = (
            credentials
            if isinstance(credentials, GeminiOAuthCredentials)
            else GeminiOAuthCredentials.parse_input(credentials)
        )
        super().__init__(api_key="", base_url=base_url, client_factory=client_factory)
        self._credentials = parsed
        self._project_id: str | None = None
        self._project_lock = asyncio.Lock()
        self._model_cooldowns: dict[str, float] = {}

    def resolve_generation_hint(self, model: str) -> tuple[str, str] | None:
        candidates = self._iter_candidate_models(model)
        if not candidates:
            return super().resolve_generation_hint(model)
        return self.name, candidates[0]

    async def chat(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str = "gemini-3-flash-preview",
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
        tool_choice: str | None = None,
    ) -> AssistantMessage:
        rc = reasoning_config or ReasoningConfig()
        thinking_budget = rc.thinking_budget if rc.thinking_budget and rc.thinking_budget > 0 else 0

        payload = self._build_payload(messages, model, tools, temperature, thinking_budget, tool_choice=tool_choice)
        last_error: Exception | None = None

        candidate_models = self._iter_candidate_models(model)

        for index, candidate_model in enumerate(candidate_models):
            request_payload = await self._build_code_assist_request(candidate_model, payload)
            url = f"{self._base_url}:generateContent"
            headers = await self._request_headers()

            try:
                async with self._client_factory() as client:
                    response = await client.post(url, json=request_payload, headers=headers)
                self._raise_for_status(response)
            except GeminiCodeAssistHTTPError as exc:
                last_error = exc
                if exc.status_code == 429:
                    self._record_model_capacity(candidate_model)
                if exc.status_code == 429 and index < len(candidate_models) - 1:
                    continue
                raise

            data = self._unwrap_generate_response(response.json())
            return self._parse_response(data, candidate_model)

        if last_error is not None:
            raise last_error
        raise RuntimeError("Gemini Code Assist request did not produce a response.")

    async def stream(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str = "gemini-3-flash-preview",
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
        tool_choice: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        rc = reasoning_config or ReasoningConfig()
        thinking_budget = rc.thinking_budget if rc.thinking_budget and rc.thinking_budget > 0 else 0

        payload = self._build_payload(messages, model, tools, temperature, thinking_budget, tool_choice=tool_choice)
        candidate_models = self._iter_candidate_models(model)
        last_error: Exception | None = None

        for index, candidate_model in enumerate(candidate_models):
            try:
                async for event in self._stream_once(payload, candidate_model):
                    yield event
                return
            except GeminiCodeAssistHTTPError as exc:
                last_error = exc
                if exc.status_code == 429:
                    self._record_model_capacity(candidate_model)
                if exc.status_code == 429 and index < len(candidate_models) - 1:
                    continue
                raise

        if last_error is not None:
            raise last_error
        raise RuntimeError("Gemini Code Assist stream did not produce a response.")

    async def _stream_once(
        self,
        payload: dict[str, Any],
        model: str,
    ) -> AsyncIterator[AgentEvent]:
        request_payload = await self._build_code_assist_request(model, payload)
        url = f"{self._base_url}:streamGenerateContent?alt=sse"
        headers = await self._request_headers()

        started = False
        text_started = False
        thinking_started = False
        tool_index = 0
        response_has_function_call = False
        done_emitted = False
        saw_any_output = False
        saw_any_candidate = False
        last_finish_reason: str | None = None
        last_block_reason: str | None = None
        buffered_lines: list[str] = []

        async with self._client_factory() as client:
            async with client.stream("POST", url, json=request_payload, headers=headers) as response:
                if response.is_error:
                    body = await response.aread()
                    detail = body.decode("utf-8", errors="replace").strip()
                    snippet = detail[:500] if detail else "<no response body>"
                    raise GeminiCodeAssistHTTPError(response.status_code, snippet)

                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if line.startswith("data:"):
                        buffered_lines.append(line[5:].strip())
                        continue
                    if line:
                        continue
                    chunk = self._parse_sse_chunk(buffered_lines)
                    buffered_lines = []
                    if chunk is None:
                        continue

                    if not started:
                        started = True
                        yield AgentEvent(type="start")

                    if "error" in chunk:
                        error = chunk["error"]
                        msg = (
                            error.get("message")
                            if isinstance(error, dict)
                            else "Gemini Code Assist stream error"
                        )
                        yield AgentEvent(type="error", error=msg)
                        continue

                    chunk_response = self._unwrap_generate_response(chunk)
                    prompt_feedback = chunk_response.get("promptFeedback")
                    if isinstance(prompt_feedback, dict):
                        block_reason = prompt_feedback.get("blockReason")
                        if isinstance(block_reason, str) and block_reason.strip():
                            last_block_reason = block_reason.strip()

                    candidates = chunk_response.get("candidates") or []
                    if not candidates:
                        continue
                    saw_any_candidate = True
                    candidate = candidates[0]
                    content = candidate.get("content") or {}
                    parts = content.get("parts") or []
                    finish_reason = candidate.get("finishReason")
                    if isinstance(finish_reason, str) and finish_reason.strip():
                        last_finish_reason = finish_reason

                    for part in parts:
                        if not isinstance(part, dict):
                            continue

                        if part.get("thought") is True and "text" in part:
                            if not thinking_started:
                                thinking_started = True
                                yield AgentEvent(type="thinking_start", content_index=0)
                            signature = part.get("thoughtSignature")
                            signature_value = signature if isinstance(signature, str) and signature.strip() else None
                            yield AgentEvent(
                                type="thinking_delta",
                                content_index=0,
                                delta=part["text"],
                                signature=signature_value,
                            )
                            saw_any_output = True
                            continue

                        if "text" in part and "functionCall" not in part:
                            if thinking_started:
                                thinking_started = False
                                yield AgentEvent(type="thinking_end", content_index=0)
                            if not text_started:
                                text_started = True
                                yield AgentEvent(type="text_start", content_index=0)
                            yield AgentEvent(type="text_delta", content_index=0, delta=part["text"])
                            saw_any_output = True
                            continue

                        function_call = part.get("functionCall")
                        if isinstance(function_call, dict):
                            response_has_function_call = True
                            if text_started:
                                text_started = False
                                yield AgentEvent(type="text_end", content_index=0)
                            call_id = f"gemini_{uuid4().hex[:8]}"
                            args = function_call.get("args") or {}
                            thought_sig = part.get("thoughtSignature")
                            thought_signature = (
                                thought_sig.strip()
                                if isinstance(thought_sig, str) and thought_sig.strip()
                                else None
                            )
                            yield AgentEvent(
                                type="toolcall_start",
                                content_index=tool_index,
                                tool_call=ToolCallContent(
                                    id=call_id,
                                    name=function_call.get("name") or "",
                                    arguments={},
                                    thought_signature=thought_signature,
                                ),
                            )
                            yield AgentEvent(
                                type="toolcall_delta",
                                content_index=tool_index,
                                delta=json.dumps(args),
                            )
                            yield AgentEvent(type="toolcall_end", content_index=tool_index)
                            tool_index += 1
                            saw_any_output = True

                    if finish_reason and not done_emitted:
                        if not response_has_function_call and not saw_any_output:
                            finish_upper = str(finish_reason).upper()
                            if finish_upper == "SAFETY" or last_block_reason:
                                reason = last_block_reason or finish_upper
                                raise RuntimeError(f"Gemini blocked response ({reason})")
                            yield AgentEvent(type="done", stop_reason=_map_finish_reason(finish_reason))
                            done_emitted = True
                            continue
                        if thinking_started:
                            yield AgentEvent(type="thinking_end", content_index=0)
                        if text_started:
                            yield AgentEvent(type="text_end", content_index=0)
                        stop = "tool_use" if response_has_function_call else _map_finish_reason(finish_reason)
                        yield AgentEvent(type="done", stop_reason=stop)
                        done_emitted = True

        if not started:
            raise TransientProviderError("Gemini Code Assist stream returned no data events")

        if not done_emitted:
            if thinking_started:
                yield AgentEvent(type="thinking_end", content_index=0)
            if text_started:
                yield AgentEvent(type="text_end", content_index=0)

            if response_has_function_call:
                yield AgentEvent(type="done", stop_reason="tool_use")
                return

            if saw_any_output:
                yield AgentEvent(type="done", stop_reason=_map_finish_reason(last_finish_reason))
                return

            if last_block_reason:
                raise RuntimeError(f"Gemini blocked response ({last_block_reason})")
            if saw_any_candidate:
                yield AgentEvent(type="done", stop_reason=_map_finish_reason(last_finish_reason))
                return
            raise TransientProviderError("Gemini Code Assist stream ended without candidates")

    async def _request_headers(self) -> dict[str, str]:
        self._assert_required_scope()
        access_token = await self._ensure_access_token()
        headers = {
            "authorization": f"Bearer {access_token}",
            "content-type": "application/json",
        }
        if self._credentials.quota_project_id:
            headers["x-goog-user-project"] = self._credentials.quota_project_id
        return headers

    def _assert_required_scope(self) -> None:
        if self._credentials.has_required_code_assist_scope():
            return
        raise RuntimeError(
            "Gemini OAuth credentials are missing the required scope "
            f"{_CODE_ASSIST_SCOPE}. "
            "Sentinel's Gemini OAuth flow simulates Gemini CLI and requires the "
            "Code Assist credentials produced by ~/.gemini/oauth_creds.json."
        )

    async def _build_code_assist_request(
        self,
        model: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        project_id = await self._ensure_project_id()
        return {
            "model": model,
            "project": project_id,
            "user_prompt_id": uuid4().hex,
            "request": payload,
        }

    async def _ensure_project_id(self) -> str:
        if self._project_id:
            return self._project_id

        async with self._project_lock:
            if self._project_id:
                return self._project_id

            seed_project = self._credentials.quota_project_id
            payload: dict[str, Any] = {
                "metadata": dict(_CODE_ASSIST_CLIENT_METADATA),
            }
            if seed_project:
                payload["cloudaicompanionProject"] = seed_project
                payload["metadata"]["duetProject"] = seed_project

            headers = await self._request_headers()
            async with self._client_factory() as client:
                response = await client.post(
                    f"{self._base_url}:loadCodeAssist",
                    json=payload,
                    headers=headers,
                )
            self._raise_for_status(response)
            data = response.json()
            project_id = data.get("cloudaicompanionProject") or seed_project
            if not isinstance(project_id, str) or not project_id.strip():
                raise RuntimeError(
                    "Gemini Code Assist login succeeded but did not return "
                    "cloudaicompanionProject."
                )
            self._project_id = project_id.strip()
            return self._project_id

    async def _ensure_access_token(self) -> str:
        access_token = self._credentials.access_token
        expiry_date = self._credentials.expiry_date
        now_ms = int(time.time() * 1000)

        if access_token:
            if expiry_date is None:
                return access_token
            if expiry_date - now_ms > _ACCESS_TOKEN_REFRESH_SKEW_SECONDS * 1000:
                return access_token

        await self._refresh_access_token()
        if not self._credentials.access_token:
            raise RuntimeError("Gemini OAuth refresh succeeded without an access token.")
        return self._credentials.access_token

    async def _refresh_access_token(self) -> None:
        refresh_token = self._credentials.refresh_token
        if not refresh_token:
            raise RuntimeError("Gemini OAuth credentials are missing refresh_token.")

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._credentials.resolved_client_id(),
            "client_secret": self._credentials.resolved_client_secret(),
        }

        async with self._client_factory() as client:
            response = await client.post(
                self._credentials.resolved_token_uri(),
                data=payload,
                headers={"content-type": "application/x-www-form-urlencoded"},
            )
        self._raise_for_status(response, label="Google OAuth token refresh")
        data = response.json()

        access_token = data.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise RuntimeError("Google token refresh response did not include access_token.")

        self._credentials.access_token = access_token.strip()

        refresh_value = data.get("refresh_token")
        if isinstance(refresh_value, str) and refresh_value.strip():
            self._credentials.refresh_token = refresh_value.strip()

        token_type = data.get("token_type")
        if isinstance(token_type, str) and token_type.strip():
            self._credentials.token_type = token_type.strip()

        scope = data.get("scope")
        if isinstance(scope, str) and scope.strip():
            self._credentials.scope = scope.strip()

        expires_in = data.get("expires_in")
        if isinstance(expires_in, int):
            self._credentials.expiry_date = int(time.time() * 1000) + (expires_in * 1000)
        elif isinstance(expires_in, float):
            self._credentials.expiry_date = int(time.time() * 1000) + int(expires_in * 1000)

    @staticmethod
    def _unwrap_generate_response(data: dict[str, Any]) -> dict[str, Any]:
        response = data.get("response")
        if isinstance(response, dict):
            return response
        return data

    @staticmethod
    def _parse_sse_chunk(buffered_lines: list[str]) -> dict[str, Any] | None:
        if not buffered_lines:
            return None
        data_blob = "\n".join(buffered_lines).strip()
        if not data_blob or data_blob == "[DONE]":
            return None
        try:
            chunk = json.loads(data_blob)
        except json.JSONDecodeError:
            return None
        if not isinstance(chunk, dict):
            return None
        return chunk

    @staticmethod
    def _raise_for_status(response: Any, *, label: str = "Gemini Code Assist") -> None:
        status_code = getattr(response, "status_code", 200)
        if status_code < 400:
            return
        detail = ""
        try:
            detail = json.dumps(response.json())
        except Exception:  # noqa: BLE001
            detail = ""
        snippet = detail[:500] if detail else "<no response body>"
        raise GeminiCodeAssistHTTPError(status_code, f"{label}: {snippet}")

    def _record_model_capacity(self, model: str) -> None:
        self._model_cooldowns[model] = time.monotonic() + _MODEL_CAPACITY_COOLDOWN_SECONDS

    def _iter_candidate_models(self, model: str) -> list[str]:
        resolved_model = _CODE_ASSIST_MODEL_RESOLUTIONS.get(model, model)
        candidates = [resolved_model]
        for fallback_model in _CODE_ASSIST_MODEL_FALLBACKS.get(resolved_model, ()):
            if fallback_model not in candidates:
                candidates.append(fallback_model)
        if len(candidates) <= 1:
            return candidates

        now = time.monotonic()
        available = [
            candidate
            for candidate in candidates
            if now >= self._model_cooldowns.get(candidate, 0.0)
        ]
        cooled = [
            candidate
            for candidate in candidates
            if now < self._model_cooldowns.get(candidate, 0.0)
        ]
        ordered = [*available, *cooled]
        return ordered or candidates
