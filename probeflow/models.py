"""Core data models for probeflow.

This module defines the Pydantic models that represent parsed HTTP requests,
environment configurations, and response data. These models form the AST
produced by the parser from the formal grammar in docs/spec.md.
"""

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


# ---------------------------------------------------------------------------
# Source location tracking
# ---------------------------------------------------------------------------


class SourceSpan(BaseModel):
    """Tracks the source location of a parsed construct.

    All positions are 1-indexed to match editor conventions.
    """

    start_line: int
    start_col: int
    end_line: int
    end_col: int

    def __str__(self) -> str:
        if self.start_line == self.end_line:
            return f"line {self.start_line}, col {self.start_col}-{self.end_col}"
        return f"line {self.start_line}:{self.start_col} - line {self.end_line}:{self.end_col}"


# ---------------------------------------------------------------------------
# Headers and body
# ---------------------------------------------------------------------------


class Header(BaseModel):
    """A single HTTP header key-value pair."""

    name: str
    value: str
    span: SourceSpan | None = None


class RequestBody(BaseModel):
    """The body of an HTTP request.

    Supports both raw string bodies and JSON-structured bodies.
    """

    content: str
    content_type: str | None = None
    span: SourceSpan | None = None

    @property
    def parsed(self) -> Any:
        """Attempt to parse the body as JSON, returning the raw string on failure."""
        if self.content_type == "application/json":
            try:
                return json.loads(self.content)
            except (json.JSONDecodeError, ValueError):
                pass
        return self.content


# ---------------------------------------------------------------------------
# Assertions (spec §7)
# ---------------------------------------------------------------------------


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
    """The left-hand-side target category of an assertion."""

    STATUS = "status"
    BODY = "body"
    HEADER = "header"
    DURATION = "duration"


class Assertion(BaseModel):
    """A single assertion within an @assert block.

    Examples:
        status == 200
        body.$.name == "Alice"
        header.Content-Type contains "application/json"
        duration < 500ms
    """

    target: AssertionTarget
    path: str | None = None
    """JSONPath or header name for body/header assertions. None for status/duration."""

    operator: AssertionOperator
    expected: Any = None
    """The expected value. None for 'exists' / 'not exists'."""

    raw_line: str = ""
    """Original assertion text for error reporting."""

    span: SourceSpan | None = None


class AssertBlock(BaseModel):
    """A collection of assertions attached to a request (spec §7)."""

    assertions: list[Assertion] = Field(default_factory=list)
    span: SourceSpan | None = None


# ---------------------------------------------------------------------------
# Script hooks (spec §6.3)
# ---------------------------------------------------------------------------


class HookRef(BaseModel):
    """A reference to a Python hook function (spec §6.3).

    Format: file_path:function_name
    Example: hooks.py:sign_request
    """

    file_path: str
    function_name: str
    span: SourceSpan | None = None


# ---------------------------------------------------------------------------
# Request and file
# ---------------------------------------------------------------------------


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


class MultipartPart(BaseModel):
    name: str
    value: str | None = None
    file_path: str | None = None
    content_type: str | None = None
    span: SourceSpan | None = None


class Request(BaseModel):
    """A parsed HTTP request from a .http file.

    Represents a single request block with its method, URL, headers,
    body, optional name, and optional environment variables.
    """

    method: HTTPMethod
    url: str
    http_version: str | None = None
    headers: list[Header] = Field(default_factory=list)
    body: RequestBody | None = None
    name: str | None = None
    environment_variables: dict[str, str] = Field(default_factory=dict)

    # probeflow extensions
    assertions: AssertBlock | None = None
    before_hook: HookRef | None = None
    after_hook: HookRef | None = None
    auth: AuthConfig | None = None
    multipart: list[MultipartPart] = Field(default_factory=list)

    span: SourceSpan | None = None

    @field_validator("method", mode="before")
    @classmethod
    def normalize_method(cls, v: str) -> str:
        """Normalize method to uppercase."""
        if isinstance(v, str):
            return v.upper()
        return v


class RequestFile(BaseModel):
    """A complete .http file containing one or more requests.

    Files are separated by `###` delimiters. Each block becomes one Request.
    """

    filename: str
    requests: list[Request] = Field(default_factory=list)
    environment_name: str | None = None


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


class Environment(BaseModel):
    """An environment configuration loaded from a .env file.

    Stores key-value pairs that are substituted into request templates.
    """

    name: str
    variables: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ParseError(Exception):
    """Raised when a .http file cannot be parsed.

    Always carries a human-readable message. Optionally carries
    line and column numbers (1-indexed) for precise location reporting.
    """

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

        # Build location prefix: "file.http:5:3: error: ..."
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
