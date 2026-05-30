from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


FieldType = Literal[
    "text",
    "textarea",
    "email",
    "url",
    "number",
    "date",
    "select",
    "badge",
    "tags",
    "readonly",
]


class ModuleFieldDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    type: FieldType = "text"
    required: bool = False
    options: list[str] | None = None


class ModuleSecretDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    required: bool = False
    hint: str | None = None
    description: str | None = None


class ModuleFieldsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    titleField: str | None = None
    subtitleField: str | None = None
    badgeField: str | None = None
    filterField: str | None = None
    metaField: str | None = None


class ModuleDefinitionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, description="Module name.")
    label: str = Field(min_length=1, description="Human-readable module name.")
    description: str = ""
    icon: str = "box"
    fields: list[ModuleFieldDefinition] = Field(default_factory=list)
    fields_config: ModuleFieldsConfig = Field(default_factory=ModuleFieldsConfig)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    secrets: list[ModuleSecretDefinition] = Field(default_factory=list)
    page_title: str | None = None
    page_content: str | None = None
    order: int = 100

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip().lower()

    @model_validator(mode="after")
    def validate_name(self) -> "ModuleDefinitionPayload":
        if not self.name:
            raise ValueError("Module name is required")
        return self

    def module_values(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class ModuleCreateRequest(ModuleDefinitionPayload):
    permissions: dict[str, Any] = Field(default_factory=dict)

    def module_values(self) -> dict[str, Any]:
        return self.model_dump(exclude={"permissions"}, exclude_none=True)


class ModuleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str | None = Field(default=None, min_length=1)
    icon: str | None = Field(default=None, min_length=1)
    fields: list[ModuleFieldDefinition] | None = None
    fields_config: ModuleFieldsConfig | None = None
    actions: list[dict[str, Any]] | None = None
    secrets: list[ModuleSecretDefinition] | None = None
    description: str | None = None
    order: int | None = None
    page_title: str | None = None
    page_content: str | None = None
    permissions: dict[str, Any] | None = None

    def module_updates(self) -> dict[str, Any]:
        return self.model_dump(exclude={"permissions"}, exclude_unset=True, exclude_none=False)


class ModuleImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    module: ModuleDefinitionPayload
    records: list[dict[str, Any]] = Field(default_factory=list)
    permissions: dict[str, Any] = Field(default_factory=dict)


def module_create_tool_parameters_schema() -> dict[str, Any]:
    return _inline_local_refs(ModuleCreateRequest.model_json_schema())


def module_name_parameter_schema() -> dict[str, Any]:
    return deepcopy(module_create_tool_parameters_schema()["properties"]["name"])


def _inline_local_refs(schema: dict[str, Any]) -> dict[str, Any]:
    root = deepcopy(schema)
    definitions = root.pop("$defs", {})

    def resolve(value: Any) -> Any:
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if not isinstance(value, dict):
            return value

        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            definition_name = ref.rsplit("/", 1)[-1]
            definition = definitions.get(definition_name)
            if isinstance(definition, dict):
                merged = {key: item for key, item in value.items() if key != "$ref"}
                return {**resolve(definition), **resolve(merged)}

        return {key: resolve(item) for key, item in value.items()}

    return resolve(root)
