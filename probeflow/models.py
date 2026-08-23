"""Core data models for probeflow."""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class HTTPMethod(str, Enum):
    """Supported HTTP methods for .http files."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


SUPPORTED_METHODS = [m.value for m in HTTPMethod]


class SourceSpan(BaseModel):
    """1-indexed source location of a parsed construct."""

    start_line: int
    start_col: int
    end_line: int
    end_col: int

    def __str__(self) -> str:
        if self.start_line == self.end_line:
            return f"line {self.start_line}, col {self.start_col}-{self.end_col}"
        return f"line {self.start_line}:{self.start_col} - line {self.end_line}:{self.end_col}"


class Header(BaseModel):
    """A single HTTP header key-value pair."""

    name: str
    value: str
    span: SourceSpan | None = None


class RequestBody(BaseModel):
    """The body of an HTTP request."""

    content: str
    content_type: str | None = None
    span: SourceSpan | None = None

    @property
    def parsed(self) -> Any:
        if self.content_type == "application/json":
            try:
                return json.loads(self.content)
            except (json.JSONDecodeError, ValueError):
                pass
        return self.content


class AssertionOperator(str, Enum):
    """Operators supported in assertion expressions."""

    EQ = "=="
    NE = "!="
    LT = "<"
    GT = ">"
    LE = "<="
    GE = ">="
    IN = "in"
    CONTAINS = "contains"
    EXISTS = "exists"
    NOT_EXISTS = "not exists"
    IS = "is"
    MATCHES = "matches"


class AssertionTarget(str, Enum):
    """Left-hand-side target category of an assertion."""

    STATUS = "status"
    BODY = "body"
    HEADER = "header"
    DURATION = "duration"


class Assertion(BaseModel):
    """A single assertion within an @assert block."""

    target: AssertionTarget
    path: str | None = None
    operator: AssertionOperator
    expected: Any = None
    raw_line: str = ""
    span: SourceSpan | None = None


class AssertBlock(BaseModel):
    """A collection of assertions attached to a request."""

    assertions: list[Assertion] = Field(default_factory=list)
    span: SourceSpan | None = None


class HookRef(BaseModel):
    """A reference to a Python hook function (file_path:function_name)."""

    file_path: str
    function_name: str
    span: SourceSpan | None = None


class OAuth2ClientCredentials(BaseModel):
    """OAuth2 client-credentials configuration for a request."""

    token_url: str
    client_id: str
    client_secret: str
    scopes: list[str] = Field(default_factory=list)
    span: SourceSpan | None = None


class MultipartPart(BaseModel):
    """One text or file part from @form or @file request metadata."""

    name: str
    value: str | None = None
    file_path: str | None = None
    content_type: str | None = None
    span: SourceSpan | None = None


class AuthScheme(str, Enum):
    BEARER = "bearer"
    BASIC = "basic"
    API_KEY = "api-key"


class ApiKeyLocation(str, Enum):
    HEADER = "header"
    QUERY = "query"


class AuthConfig(BaseModel):
    scheme: AuthScheme
    token: str | None = None
    username: str | None = None
    password: str | None = None
    api_key_location: ApiKeyLocation | None = None
    api_key_name: str | None = None
    api_key_value: str | None = None
    span: SourceSpan | None = None


class Request(BaseModel):
    """A parsed HTTP request from a .http file."""

    method: HTTPMethod
    url: str
    http_version: str | None = None
    headers: list[Header] = Field(default_factory=list)
    body: RequestBody | None = None
    name: str | None = None
    environment_variables: dict[str, str] = Field(default_factory=dict)
    assertions: AssertBlock | None = None
    before_hook: HookRef | None = None
    after_hook: HookRef | None = None
    auth: AuthConfig | None = None
    oauth2: OAuth2ClientCredentials | None = None
    multipart: list[MultipartPart] = Field(default_factory=list)
    span: SourceSpan | None = None

    @field_validator("method", mode="before")
    @classmethod
    def normalize_method(cls, v: str) -> str:
        if isinstance(v, str):
            return v.upper()
        return v


class RequestFile(BaseModel):
    """A complete .http file containing one or more requests."""

    filename: str
    requests: list[Request] = Field(default_factory=list)
    environment_name: str | None = None


class Environment(BaseModel):
    """Environment configuration key-value pairs."""

    name: str
    variables: dict[str, str] = Field(default_factory=dict)


class ParseError(Exception):
    """Raised when a .http file cannot be parsed."""

    def __init__(
        self,
        message: str,
        line: int | None = None,
        column: int | None = None,
        filename: str | None = None,
    ) -> None:
        self.line = line
        self.column = column
        self.filename = filename

        parts: list[str] = []
        if filename:
            parts.append(filename)
        if line is not None:
            parts.append(str(line))
            if column is not None:
                parts.append(str(column))

        if parts:
            prefix = ":".join(parts)
            full_message = f"{prefix}: error: {message}"
        else:
            full_message = f"error: {message}"

        super().__init__(full_message)
        self.raw_message = message
