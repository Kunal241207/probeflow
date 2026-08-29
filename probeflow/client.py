"""HTTP client for executing requests."""

from __future__ import annotations

import mimetypes
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from probeflow.models import OAuth2ClientCredentials, Request


@dataclass
class Response:
    """A structured HTTP response with metadata."""

    status_code: int
    status_text: str
    elapsed_ms: float
    size_bytes: int
    headers: dict[str, str]
    body: str
    parsed_body: Any = None
    json_parsed: bool = False
    content_type: str | None = None
    url: str = ""


class OAuth2Error(RuntimeError):
    """Raised when OAuth2 client-credentials authentication cannot proceed."""


@dataclass
class _AccessToken:
    value: str
    token_type: str
    expires_at: float


class OAuth2TokenProvider:
    """Per-run, in-memory OAuth2 client-credentials token cache."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._tokens: dict[tuple[str, str, str, tuple[str, ...]], _AccessToken] = {}

    def authorization_header(self, config: OAuth2ClientCredentials, timeout: float) -> str:
        key = (config.token_url, config.client_id, config.client_secret, tuple(config.scopes))
        token = self._tokens.get(key)
        if token is None or token.expires_at - self._clock() <= 30:
            token = self._fetch_token(config, timeout)
            self._tokens[key] = token
        return f"{token.token_type} {token.value}"

    def _fetch_token(self, config: OAuth2ClientCredentials, timeout: float) -> _AccessToken:
        payload = {"grant_type": "client_credentials"}
        if config.scopes:
            payload["scope"] = " ".join(config.scopes)
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    config.token_url,
                    data=payload,
                    auth=(config.client_id, config.client_secret),
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OAuth2Error(f"OAuth2 token request failed: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise OAuth2Error("OAuth2 token response was not valid JSON") from exc
        access_token = data.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuth2Error("OAuth2 token response did not include access_token")

        try:
            expires_in = float(data.get("expires_in", 0))
        except (TypeError, ValueError) as exc:
            raise OAuth2Error("OAuth2 token response has an invalid expires_in value") from exc

        token_type = data.get("token_type", "Bearer")
        if not isinstance(token_type, str) or not token_type:
            raise OAuth2Error("OAuth2 token response has an invalid token_type")
        return _AccessToken(
            value=access_token,
            token_type=token_type,
            expires_at=self._clock() + max(expires_in, 0),
        )


_STATUS_TEXTS: dict[int, str] = {
    100: "Continue",
    101: "Switching Protocols",
    200: "OK",
    201: "Created",
    204: "No Content",
    301: "Moved Permanently",
    302: "Found",
    304: "Not Modified",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    408: "Request Timeout",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


def _status_text(code: int) -> str:
    return _STATUS_TEXTS.get(code, f"Status {code}")


def _try_parse_json(body: str, content_type: str | None) -> tuple[Any, bool]:
    """Parse a JSON body when the content type is JSON; return (value, ok)."""
    if content_type and "json" in content_type.lower():
        import json

        try:
            return json.loads(body), True
        except (json.JSONDecodeError, ValueError):
            pass
    return None, False


def execute_request(
    request: Request,
    timeout: float = 30.0,
    follow_redirects: bool = True,
    token_provider: OAuth2TokenProvider | None = None,
    base_dir: Path | None = None,
) -> Response:
    """Execute an HTTP request and return a structured response."""
    headers = {h.name: h.value for h in request.headers}
    if request.oauth2:
        if any(name.lower() == "authorization" for name in headers):
            raise OAuth2Error("@oauth2 cannot be combined with an explicit Authorization header")
        provider = token_provider or OAuth2TokenProvider()
        headers["Authorization"] = provider.authorization_header(request.oauth2, timeout)

    body: str | bytes | None = None
    multipart_files: list[tuple[str, tuple[str | None, str | bytes, str | None]]] | None = None
    if request.multipart:
        if request.body:
            raise ValueError("Multipart requests cannot also set a request body")
        if any(name.lower() == "content-type" for name in headers):
            raise ValueError("Multipart requests cannot set Content-Type explicitly")
        multipart_files = []
        for part in request.multipart:
            if part.file_path is None:
                multipart_files.append((part.name, (None, part.value or "", None)))
                continue

            root_dir = (base_dir or Path.cwd()).resolve()
            raw_path = Path(part.file_path)
            resolved_path = (
                raw_path.resolve() if raw_path.is_absolute() else (root_dir / raw_path).resolve()
            )

            try:
                resolved_path.relative_to(root_dir)
            except ValueError:
                raise ValueError(
                    f"Multipart file path '{part.file_path}' "
                    f"attempts traversal outside base directory '{root_dir}'"
                )

            if not resolved_path.is_file():
                raise FileNotFoundError(f"Multipart file not found: {resolved_path}")
            content_type = part.content_type or mimetypes.guess_type(resolved_path.name)[0]
            multipart_files.append(
                (
                    part.name,
                    (
                        resolved_path.name,
                        resolved_path.read_bytes(),
                        content_type or "application/octet-stream",
                    ),
                )
            )
    if request.body:
        body = request.body.content

    start = time.perf_counter()

    try:
        with httpx.Client(timeout=timeout, follow_redirects=follow_redirects) as client:
            response = client.request(
                method=request.method.value,
                url=request.url,
                headers=headers,
                content=body,
                files=multipart_files,
            )
    except httpx.ConnectTimeout:
        raise ConnectionError(f"Connection timed out after {timeout}s: {request.url}")
    except httpx.ConnectError as e:
        raise ConnectionError(f"Connection failed: {request.url} ({e})")

    elapsed_ms = (time.perf_counter() - start) * 1000
    response_body = response.text
    content_type = response.headers.get("content-type", "")

    parsed_body, json_parsed = _try_parse_json(response_body, content_type)

    return Response(
        status_code=response.status_code,
        status_text=_status_text(response.status_code),
        elapsed_ms=round(elapsed_ms, 1),
        size_bytes=len(response.content),
        headers=dict(response.headers),
        body=response_body,
        parsed_body=parsed_body,
        json_parsed=json_parsed,
        content_type=content_type,
        url=str(response.url),
    )
