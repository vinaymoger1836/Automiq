"""Bounded CloudEvents and runtime-configured custom app data contracts."""

import ipaddress
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

EVENT_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{2,79}$")
FIELD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")


class ScalarSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["string", "integer", "number", "boolean", "null"]


class DataSchema(BaseModel):
    """A deliberately bounded, JSON Schema-compatible object subset."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["object"] = "object"
    properties: dict[str, ScalarSchema] = Field(default_factory=dict, max_length=30)
    required: list[str] = Field(default_factory=list, max_length=30)
    additional_properties: Literal[False] = Field(default=False, alias="additionalProperties")

    @model_validator(mode="after")
    def valid_fields(self) -> "DataSchema":
        if any(not FIELD_NAME.fullmatch(name) for name in self.properties):
            raise ValueError("Schema property names must be simple identifiers")
        if (
            len(set(self.required)) != len(self.required)
            or not set(self.required) <= self.properties.keys()
        ):
            raise ValueError("Required properties must be unique declared properties")
        return self


class CloudEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    specversion: Literal["1.0"]
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._:-]+$")
    source: str = Field(min_length=1, max_length=255)
    type: str = Field(min_length=3, max_length=80)
    subject: str | None = Field(default=None, max_length=255)
    datacontenttype: Literal["application/json"] | None = None
    data: dict[str, Any]

    @model_validator(mode="after")
    def valid_event(self) -> "CloudEvent":
        if not EVENT_TYPE.fullmatch(self.type):
            raise ValueError("Invalid event type")
        parsed = urlsplit(self.source)
        if not parsed.scheme or any(char.isspace() for char in self.source):
            raise ValueError("Event source must be an absolute URI")
        if len(self.data) > 30:
            raise ValueError("Event data has too many fields")
        return self


def validate_data(data: dict[str, Any], schema: DataSchema) -> None:
    if set(data) - schema.properties.keys() or not set(schema.required) <= data.keys():
        raise ValueError("Event data fields do not match the registered schema")
    for name, value in data.items():
        kind = schema.properties[name].type
        valid = (
            isinstance(value, str)
            if kind == "string"
            else isinstance(value, int) and not isinstance(value, bool)
            if kind == "integer"
            else isinstance(value, (int, float)) and not isinstance(value, bool)
            if kind == "number"
            else isinstance(value, bool)
            if kind == "boolean"
            else value is None
        )
        if not valid or (isinstance(value, str) and len(value) > 2000):
            raise ValueError("Event data field has an invalid type or size")


def validate_event_types(schemas: dict[str, DataSchema]) -> None:
    if len(schemas) > 20 or any(not EVENT_TYPE.fullmatch(name) for name in schemas):
        raise ValueError("Invalid event types")


def validate_callback_url(value: str | None, *, test_mode: bool) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    try:
        port = parsed.port or 443
    except ValueError:
        port = 0
    if (
        parsed.scheme != "https"
        or not host
        or is_ip
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*", host)
        or host.lower() in {"localhost", "localhost.localdomain"}
        or host.lower().endswith((".local", ".internal", ".localhost"))
        or (not test_mode and ("." not in host or port != 443))
        or port < 1
        or not re.fullmatch(r"/(?:[A-Za-z0-9_-]+/?)*", parsed.path or "/")
    ):
        raise ValueError("Callback must be an approved named HTTPS endpoint")
    return value
