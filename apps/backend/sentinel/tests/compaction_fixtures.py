import json

from sentral.llm.generic.base import LLMProvider
from sentral.llm.generic.types import AgentEvent, AssistantMessage, TextContent


def handoff_response(messages):
    if "Independently verify" in messages[0]["content"]:
        return json.dumps({"approved": True, "issues": []})
    data = json.loads(messages[-1]["content"])
    source = data["source_messages"][0]["message_id"]
    item = {"text": "Preserved engineering detail", "sources": [source]}
    return json.dumps(
        {
            "overview": "Compact summary",
            "constraints": [item],
            "decisions": [item],
            "workstreams": [{"name": "Implementation", "status": "in progress", "details": [item]}],
            "artifacts": [item],
            "results": [item],
            "next_steps": [item],
            "pending_obligations": [],
            "superseded": [],
        }
    )


class HandoffProvider(LLMProvider):
    """Returns a schema-valid handoff and approves its own audit."""

    @property
    def name(self) -> str:
        return "handoff"

    async def chat(
        self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None
    ):
        return AssistantMessage(
            content=[TextContent(text=handoff_response(messages))],
            model=model,
            provider=self.name,
        )

    async def stream(
        self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None
    ):
        yield AgentEvent(type="start")
        yield AgentEvent(type="done", stop_reason="stop")
