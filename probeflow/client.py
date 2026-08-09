"""HTTP client for executing requests.

Wraps httpx to send parsed requests and return structured responses
with timing, size, and status information.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from probeflow.models import Request


@dataclass
class Response:
    """A structured HTTP response with metadata.

    Attributes:
        status_code: The HTTP status code.
        status_text: Human-readable status text (e.g., "OK", "Not Found").
        elapsed_ms: Request duration in milliseconds.
        size_bytes: Size of the response body in bytes.
        headers: Response headers as a dictionary.
        body: The response body as a string.
        parsed_body: The parsed JSON body, or None if not JSON.
        content_type: The Content-Type of the response.
        url: The final URL (after redirects).
    """

    status_code: int
    status_text: str
    elapsed_ms: float
    size_bytes: int
    headers: dict[str, str]
    body: str
    parsed_body: Any = None
    content_type: str | None = None
    url: str = ""


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
    """Get human-readable status text for a code, or a generic description."""
    return _STATUS_TEXTS.get(code, f"Status {code}")


def _try_parse_json(body: str, content_type: str | None) -> Any:
    """Attempt to parse the body as JSON.

    Returns None if the content type is not JSON or parsing fails.
    """
    if content_type and "json" in content_type.lower():
        import json

        try:
            return json.loads(body)
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _build_multipart_files(
    request: Request,
    base_dir: Path | None,
) -> list[tuple[str, Any]]:
    """Build the files list for httpx multipart encoding."""
    files: list[tuple[str, Any]] = []
    for part in request.multipart:
        if part.file_path is not None:
            resolved = (Path(base_dir) / part.file_path) if base_dir else Path(part.file_path)
            data = resolved.read_bytes()
            content_type = part.content_type or "application/octet-stream"
            files.append((part.name, (part.file_path, data, content_type)))
        else:
            files.append((part.name, (None, part.value or "", part.content_type or "text/plain")))
    return files


def execute_request(
    request: Request,
    timeout: float = 30.0,
    follow_redirects: bool = True,
    base_dir: Path | None = None,
) -> Response:
    """Execute an HTTP request and return a structured response.

    Args:
        request: The resolved request to send.
        timeout: Request timeout in seconds.
        follow_redirects: Whether to follow HTTP redirects.
        base_dir: Base directory for resolving relative file paths in multipart parts.

    Returns:
        A Response object with full response metadata.

    Raises:
        ConnectionError: On network or timeout errors.
    """
    headers = {h.name: h.value for h in request.headers}

    start = time.perf_counter()

    try:
        with httpx.Client(timeout=timeout, follow_redirects=follow_redirects) as client:
            if request.multipart:
                files = _build_multipart_files(request, base_dir)
                response = client.request(
                    method=request.method.value,
                    url=request.url,
                    headers=headers,
                    files=files,
                )
            else:
                body: str | bytes | None = request.body.content if request.body else None
                response = client.request(
                    method=request.method.value,
                    url=request.url,
                    headers=headers,
                    content=body,
                )
    except httpx.ConnectTimeout:
        raise ConnectionError(f"Connection timed out after {timeout}s: {request.url}")
    except httpx.ConnectError as e:
        raise ConnectionError(f"Connection failed: {request.url} ({e})")

    elapsed_ms = (time.perf_counter() - start) * 1000

    response_body = response.text
    content_type = response.headers.get("content-type", "")

    return Response(
        status_code=response.status_code,
        status_text=_status_text(response.status_code),
        elapsed_ms=round(elapsed_ms, 1),
        size_bytes=len(response.content),
        headers=dict(response.headers),
        body=response_body,
        parsed_body=_try_parse_json(response_body, content_type),
        content_type=content_type,
        url=str(response.url),
    )
