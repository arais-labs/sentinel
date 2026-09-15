from uuid import uuid4

from app.services.modules.definitions import ActionDefinition, ModuleDefinition

from .contract import Form, Option, Question


async def present(payload):
    form = Form.model_validate(payload)
    return {
        "kind": "form",
        "form_id": str(uuid4()),
        "status": "awaiting_input",
        **form.model_dump(),
    }


def form_schema():
    schema = Form.model_json_schema()
    question = Question.model_json_schema()
    question["properties"]["options"]["items"] = Option.model_json_schema()
    question.pop("$defs", None)
    schema["properties"]["questions"]["items"] = question
    schema.pop("$defs", None)
    return schema


MODULE = ModuleDefinition(
    name="form",
    label="Form",
    icon="list-checks",
    system=True,
    grouped_tool=True,
    description="Ask the user structured questions and wait for their answers before continuing.",
    actions=[
        ActionDefinition(
            id="present",
            label="Ask questions",
            handler=present,
            permission_default="allow",
            description="Present a question tree. Use parent_id=null for top-level questions and a question ID for children; sibling order follows the array. Finish each subtree before the next root. All questions are answered; Provide useful suggested answers when appropriate. Every question also accepts arbitrary text, alone or alongside a choice. The user can dismiss the form without answering. Use this alone, then wait for the user.",
            parameters_schema=form_schema(),
        )
    ],
)
