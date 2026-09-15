"""Depth-first question trees and their user responses."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, literal_column, or_, select

from app.models import Message


class Option(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = ""


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    parent_id: str | None = None
    question: str = Field(min_length=1)
    options: list[Option] = Field(default_factory=list)


class Form(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    questions: list[Question] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_tree(self):
        by_id = {q.id: q for q in self.questions}
        if len(by_id) != len(self.questions):
            raise ValueError("Question IDs must be unique")
        for q in self.questions:
            if len({o.id for o in q.options}) != len(q.options):
                raise ValueError("Option IDs must be unique within each question")
            seen = {q.id}
            parent = q.parent_id
            while parent is not None:
                if parent not in by_id or parent in seen:
                    raise ValueError("Question parents must exist and form an acyclic tree")
                seen.add(parent)
                parent = by_id[parent].parent_id
        return self

    def ordered(self):
        children: dict[str | None, list[Question]] = {}
        for q in self.questions:
            children.setdefault(q.parent_id, []).append(q)
        stack = list(reversed(children.get(None, [])))
        result = []
        while stack:
            q = stack.pop()
            result.append(q)
            stack.extend(reversed(children.get(q.id, [])))
        return result


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str
    option_id: str | None = None
    text: str = ""


class FormResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    form_id: str
    status: Literal["answered", "dismissed"] = "answered"
    answers: list[Answer] = Field(default_factory=list)


def format_answers(form: Form, response: FormResponse) -> str:
    if response.status == "dismissed":
        if response.answers:
            raise ValueError("A dismissed form must not submit answers")
        return f"The user dismissed the form: {form.title}. No answers were submitted. Do not treat suggested choices as consent."
    ordered = form.ordered()
    if [a.question_id for a in response.answers] != [q.id for q in ordered]:
        raise ValueError("Answer every question in depth-first order")
    lines = [f"Answers to {form.title}:"]
    for question, answer in zip(ordered, response.answers):
        options = {o.id: o for o in question.options}
        if answer.option_id is not None and answer.option_id not in options:
            raise ValueError("Unknown answer option")
        choice = options[answer.option_id].label if answer.option_id is not None else ""
        text = answer.text.strip()
        if not choice and not text:
            raise ValueError("Each question needs a choice or written answer")
        lines.append(
            f"{question.id}. {question.question}\nAnswer: "
            + " — ".join(x for x in (choice, text) if x)
        )
    return "\n\n".join(lines)


async def validate_response(db, session_id, payload):
    response = FormResponse.model_validate(payload)
    result = await db.execute(select(Message).where(Message.session_id == session_id))
    form = None
    for message in result.scalars().all():
        answered = (message.metadata_json or {}).get("form_response") or {}
        if message.role == "user" and answered.get("form_id") == response.form_id:
            raise ValueError("This form has already been resolved")
        if message.role != "tool_result" or message.tool_name != "form":
            continue
        try:
            data = json.loads(message.content)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and data.get("form_id") == response.form_id:
            form = Form.model_validate({"title": data["title"], "questions": data["questions"]})
    if form is None:
        raise ValueError("Form not found in this session")
    return format_answers(form, response), response.model_dump()


async def pending_form_sessions(db, session_ids):
    if not session_ids:
        return {}
    result = await db.execute(
        select(Message)
        .where(
            Message.session_id.in_(session_ids),
            or_(
                and_(Message.role == "tool_result", Message.tool_name == "form"),
                and_(
                    Message.role == "user",
                    Message.metadata_json["form_response"].as_string().is_not(None),
                ),
            ),
        )
        .order_by(Message.created_at, literal_column("messages.rowid"))
    )
    latest = {}
    resolved = set()
    for message in result.scalars().all():
        if message.role == "user":
            response = (message.metadata_json or {}).get("form_response")
            if isinstance(response, dict):
                resolved.add((message.session_id, response.get("form_id")))
        elif message.role == "tool_result" and message.tool_name == "form":
            try:
                form = json.loads(message.content)
            except (ValueError, TypeError):
                continue
            if isinstance(form, dict) and form.get("kind") == "form" and form.get("form_id"):
                latest[message.session_id] = form["form_id"]
    return {
        session_id: form_id
        for session_id, form_id in latest.items()
        if (session_id, form_id) not in resolved
    }
