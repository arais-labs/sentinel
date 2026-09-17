"""Quality-first, bounded handoff generation. No database writes or agent tools."""

import asyncio
import json
from dataclasses import replace
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictBool

from app.services.llm.session_selection import selection_model
from app.services.llm.tier import TierModelConfig, TierProvider
from app.services.sessions.handoff import (
    AUDIT_INSTRUCTIONS,
    HANDOFF_INSTRUCTIONS,
    Handoff,
    parse_object,
    render_summary,
    validate_handoff,
)
from sentral.llm.generic.types import ReasoningConfig, TextContent


class Audit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: StrictBool
    issues: list[str]


def current_selection(messages) -> tuple[str, str | None]:
    for message in reversed(messages):
        metadata = message.metadata_json or {}
        generation = metadata.get("generation") or {}
        selection = metadata.get("model_selection") or generation.get("model_selection") or {}
        provider = selection.get("provider_id") or generation.get("provider")
        if generation.get("requested_tier") or selection:
            requested = generation.get("requested_tier")
            if isinstance(requested, str) and requested.startswith("sentinel:"):
                parts = requested.split(":")
                return requested, None if parts[2] == "auto" else parts[2]
            return (
                selection_model(
                    requested,
                    provider,
                    selection.get("reasoning_level"),
                    selection.get("fast_mode", False),
                ),
                provider,
            )
        model = generation.get("resolved_model") or (metadata.get("provider_usage") or {}).get(
            "model"
        )
        if model:
            return model, provider
    return "normal", None


def source_record(message) -> dict:
    metadata = message.metadata_json or {}
    return {
        "message_id": str(message.id),
        "role": message.role,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "provenance": {
            key: metadata[key]
            for key in (
                "source",
                "notice",
                "steering",
                "voice_request_id",
                "telegram_is_owner",
                "telegram_chat_type",
                "source_session_id",
            )
            if key in metadata
        },
        "content": message.content,
        "tool_name": message.tool_name,
        "tool_call_id": message.tool_call_id,
        "tool_calls": metadata.get("tool_calls"),
    }


def batches(records: list[dict], byte_budget: int):
    """Conservative UTF-8 byte budget; no guessed token ratios or silent truncation."""
    batch, size = [], 0
    for record in records:
        text = json.dumps(record, ensure_ascii=False)
        length = len(text.encode("utf-8")) + 2
        if length > byte_budget:
            raise ValueError(
                "One history message exceeds the compaction input budget; history is unchanged."
            )
        if batch and size + length > byte_budget:
            yield batch
            batch, size = [], 0
        batch.append(record)
        size += length
    if batch:
        yield batch


async def generate_handoff(
    provider, messages, previous: dict | None, selection: tuple[str, str | None], *, trace=None
):
    if provider is None:
        raise ValueError("Compaction requires a configured model; history was not changed.")
    current, provider_id = selection
    selections = [selection_model("hard", provider_id), current]
    failures, seen = [], set()
    traces = trace if trace is not None else []
    for selected in dict.fromkeys(selections):
        try:
            config = (
                provider.resolve_model_config(selected)
                if isinstance(provider, TierProvider)
                else TierModelConfig(provider, selected, ReasoningConfig())
            )
            identity = (config.provider.name, config.model)
            if identity in seen:
                continue
            seen.add(identity)
            payload = await _generate(config, messages, previous, traces)
            payload["compaction_selection"] = {"requested": selected, "fallbacks": failures}
            return payload
        except Exception as exc:
            # Cancellation is BaseException: never turn cancellation into another paid request.
            failures.append({"selection": selected, "error_type": type(exc).__name__})
    raise ValueError(
        "Compaction failed generation, verification, or budget checks with both the strong "
        "and current model; history was not changed. "
        + "; ".join(f"{item['selection']}: {item['error_type']}" for item in failures)
    )


async def _generate(config, messages, previous, traces):
    provider, model = config.provider, config.model
    context = provider.model_context(model)
    window = context.get("context_window_tokens") or provider.capabilities().max_context_tokens
    reserve = min(32768, max(8192, window // 8))
    # Includes any provider-specific thinking reserve; do not mutate shared configs.
    reasoning = replace(config.reasoning_config, max_tokens=reserve)
    if reasoning.thinking_budget and reasoning.thinking_budget >= reserve:
        reasoning = replace(reasoning, thinking_budget=reserve // 2)
    handoff_budget = min(100_000, window // 5)
    prior = render_summary(previous)
    if len(prior.encode("utf-8")) > handoff_budget:
        raise ValueError("Previous handoff exceeds this model's safe compaction budget.")
    input_budget = window - reserve - 2 * handoff_budget - 16000
    if input_budget < 4096:
        raise ValueError("Model context is too small for a verified handoff.")
    known = set()
    if previous and previous.get("schema_version") == 2:
        known = {
            source
            for item in Handoff.model_validate(previous["handoff"]).evidence()
            for source in item.sources
        }
    result = None
    schema = json.dumps(Handoff.model_json_schema())

    async def call(instruction, data, phase):
        prompt = [
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
        ]
        # Byte count is conservative for tokenizers; provider count, when available,
        # also catches transport-specific context accounting.
        if sum(len(item["content"].encode("utf-8")) for item in prompt) > window - reserve - 4096:
            raise ValueError("Compaction request exceeds its safe context budget.")
        count = await provider.count_input_tokens(
            prompt, model, tools=[], reasoning_config=reasoning
        )
        if count is not None and count > window - reserve - 4096:
            raise ValueError("Provider counted too many compaction input tokens.")
        async with asyncio.timeout(600):
            response = await provider.chat(
                prompt,
                model=model,
                tools=[],
                tool_choice="none",
                temperature=0.2,
                reasoning_config=reasoning,
            )
        traces.append(
            {
                "phase": phase,
                "provider": response.provider,
                "model": response.model,
                "provider_usage": getattr(response, "provider_usage", None),
            }
        )
        if response.stop_reason not in {"stop", "end_turn"}:
            raise ValueError("Compaction response did not finish normally.")
        raw = "\n".join(block.text for block in response.content if isinstance(block, TextContent))
        return parse_object(raw)

    for batch in batches([source_record(message) for message in messages], input_budget):
        known.update(UUID(record["message_id"]) for record in batch)
        data = {"previous_handoff": prior, "source_messages": batch}
        draft = await call(HANDOFF_INSTRUCTIONS + "\nJSON schema:\n" + schema, data, "draft")
        for attempt in range(2):
            handoff = validate_handoff(draft, known)
            result = {"schema_version": 2, "handoff": handoff.model_dump(mode="json")}
            rendered = render_summary(result)
            if len(rendered.encode("utf-8")) > handoff_budget:
                raise ValueError("Generated handoff exceeds the continuation budget.")
            audit = Audit.model_validate(
                await call(AUDIT_INSTRUCTIONS, {**data, "proposed_handoff": draft}, "audit")
            )
            if audit.approved and not audit.issues:
                break
            if attempt:
                raise ValueError("Compaction handoff failed its independent verification pass.")
            draft = await call(
                HANDOFF_INSTRUCTIONS + "\nJSON schema:\n" + schema,
                {**data, "previous_draft": draft, "fix_these_issues": audit.issues},
                "repair",
            )
        prior = rendered
    if result is None:
        raise ValueError("No messages to summarize.")
    result["generation_trace"] = traces
    result["summary_text"] = render_summary(result)
    return result
