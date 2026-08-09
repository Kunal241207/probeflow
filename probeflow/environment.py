"""Environment variable handling for .http files.

Loads variables from .env files and provides {{VARIABLE}} substitution
into request URLs, headers, and bodies.
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from probeflow.models import AuthScheme, Environment, Header, Request, RequestBody

# Matches {{variable}} patterns in strings
_VARIABLE_PATTERN = re.compile(r"\{\{\s*([^{}\s]+)\s*\}\}")

# Default .env file names, searched in order
_DEFAULT_ENV_FILES = [
    ".env",
    ".env.local",
]

# Per-environment .env file pattern
_ENV_FILE_PATTERN = ".env.{}"


class EnvironmentNotFoundError(Exception):
    """Raised when a requested environment file cannot be found."""

    pass


class VariableResolutionError(Exception):
    """Raised when a response-chaining reference cannot be resolved."""


def _resolve_response_reference(reference: str, responses: dict) -> str:
    """Resolve ``request.response`` references against prior responses."""
    match = re.fullmatch(
        r"(?P<request>[A-Za-z][A-Za-z0-9_-]*)\.response\.(?P<field>.+)",
        reference,
    )
    if not match:
        raise VariableResolutionError(f"Invalid response reference: {{{{{reference}}}}}")

    request_name = match.group("request")
    response = responses.get(request_name)
    if response is None:
        raise VariableResolutionError(
            f"Response reference '{reference}' requires a preceding successful request "
            f"named '{request_name}'"
        )

    field = match.group("field")
    if field == "status":
        value = response.status_code
    elif field.startswith("headers."):
        header_name = field.removeprefix("headers.")
        value = next(
            (
                header_value
                for name, header_value in response.headers.items()
                if name.lower() == header_name.lower()
            ),
            None,
        )
    elif field.startswith("body."):
        from probeflow.evaluator import _extract_jsonpath

        path = field.removeprefix("body.")
        # Ensure the path starts with $ for _extract_jsonpath
        if not path.startswith("$"):
            path = "$." + path.lstrip(".")
        if response.parsed_body is None:
            value = None
        else:
            value = _extract_jsonpath(response.parsed_body, path)
    else:
        raise VariableResolutionError(
            f"Unsupported response reference field in '{{{{{reference}}}}}'"
        )

    if value is None:
        raise VariableResolutionError(
            f"Response reference '{{{{{reference}}}}}' did not resolve to a value"
        )
    return str(value)


def load_env_file(filepath: Path) -> dict[str, str]:
    """Load environment variables from a .env file.

    Uses python-dotenv for parsing. Supports standard .env syntax
    including quoted values, comments, and blank lines.

    Args:
        filepath: Path to the .env file.

    Returns:
        Dictionary of variable names to values.
    """
    if not filepath.exists():
        raise EnvironmentNotFoundError(f"Environment file not found: {filepath}")

    from dotenv import dotenv_values

    values = dotenv_values(filepath)
    # Filter out None values (comments and empty lines)
    return {k: v for k, v in values.items() if v is not None}


def find_env_file(
    directory: Path,
    env_name: str | None = None,
) -> Path | None:
    """Find the appropriate .env file for the given environment.

    If env_name is None, tries default .env files.
    If env_name is provided, tries .env.{env_name} first, then falls back to defaults.

    Args:
        directory: The directory to search for .env files (typically same dir as .http file).
        env_name: Optional environment name (e.g., "dev", "staging", "prod").

    Returns:
        Path to the .env file, or None if not found.
    """
    if env_name:
        specific = directory / _ENV_FILE_PATTERN.format(env_name)
        if specific.exists():
            return specific

    for default in _DEFAULT_ENV_FILES:
        candidate = directory / default
        if candidate.exists():
            return candidate

    return None


def load_environment(
    directory: Path,
    env_name: str | None = None,
) -> Environment | None:
    """Load an Environment from a .env file.

    Args:
        directory: Directory to search for .env files.
        env_name: Optional environment name.

    Returns:
        An Environment object, or None if no .env file was found.
    """
    env_file = find_env_file(directory, env_name)
    if env_file is None:
        return None

    variables = load_env_file(env_file)
    return Environment(name=env_name or "default", variables=variables)


def substitute_variables(
    text: str,
    variables: dict[str, str],
    responses: dict | None = None,
) -> str:
    """Replace {{variable}} placeholders in a string.

    Unresolved variables are left as-is so the user can see what
    is missing. System environment variables are used as a fallback.

    Args:
        text: The string containing {{variable}} placeholders.
        variables: Dictionary of variable names to values.
        responses: Optional dict of prior named responses for chaining.

    Returns:
        The string with variables substituted.
    """

    def _replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if ".response." in var_name:
            return _resolve_response_reference(var_name, responses or {})
        if var_name in variables:
            return variables[var_name]
        sys_val = os.environ.get(var_name)
        if sys_val is not None:
            return sys_val
        return match.group(0)

    return _VARIABLE_PATTERN.sub(_replacer, text)


def _apply_auth(
    request: Request,
    variables: dict[str, str],
    resolved_headers: list[Header],
) -> list[Header]:
    """Apply auth config to the resolved headers list, returning a new list."""
    if request.auth is None:
        return resolved_headers

    # Check for explicit Authorization header conflict
    has_auth_header = any(h.name.lower() == "authorization" for h in resolved_headers)
    if has_auth_header:
        raise VariableResolutionError(
            "Auth config would override an explicit Authorization header. "
            "Remove the Authorization header or the @auth directive."
        )

    auth = request.auth
    new_headers = list(resolved_headers)

    if auth.scheme == AuthScheme.BEARER:
        token = substitute_variables(auth.token or "", variables)
        new_headers.append(Header(name="Authorization", value=f"Bearer {token}"))

    elif auth.scheme == AuthScheme.BASIC:
        username = substitute_variables(auth.username or "", variables)
        password = substitute_variables(auth.password or "", variables)
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
        new_headers.append(Header(name="Authorization", value=f"Basic {encoded}"))

    return new_headers


def _apply_api_key_query(url: str, key_name: str, key_value: str) -> str:
    """Append an API key query parameter to a URL."""
    parsed = urlparse(url)
    existing = parse_qs(parsed.query, keep_blank_values=True)
    existing[key_name] = [key_value]
    new_query = urlencode({k: v[0] for k, v in existing.items()})
    return urlunparse(parsed._replace(query=new_query))


def resolve_request(
    request: Request,
    variables: dict[str, str],
    responses: dict | None = None,
) -> Request:
    """Resolve all variable placeholders in a request.

    Substitutes variables in the URL, header names/values, and body content.
    Also applies auth config and validates it does not conflict with explicit headers.

    Args:
        request: The request with unresolved variable placeholders.
        variables: Dictionary of variable names to values.
        responses: Optional dict of prior named responses for chaining.

    Returns:
        A new Request with all placeholders resolved.

    Raises:
        VariableResolutionError: If a response reference cannot be resolved,
            or if auth config conflicts with an explicit Authorization header.
    """
    resolved_url = substitute_variables(request.url, variables, responses)

    resolved_headers = [
        Header(
            name=substitute_variables(h.name, variables, responses),
            value=substitute_variables(h.value, variables, responses),
        )
        for h in request.headers
    ]

    resolved_body: RequestBody | None = None
    if request.body:
        resolved_body = RequestBody(
            content=substitute_variables(request.body.content, variables, responses),
            content_type=request.body.content_type,
        )

    # Apply auth (may raise VariableResolutionError on conflict)
    if request.auth is not None:
        from probeflow.models import ApiKeyLocation

        auth = request.auth
        if auth.scheme == AuthScheme.BEARER or auth.scheme == AuthScheme.BASIC:
            resolved_headers = _apply_auth(request, variables, resolved_headers)
        elif auth.scheme == AuthScheme.API_KEY:
            key_name = auth.api_key_name or ""
            key_value = substitute_variables(auth.api_key_value or "", variables)
            if auth.api_key_location == ApiKeyLocation.HEADER:
                resolved_headers.append(Header(name=key_name, value=key_value))
            elif auth.api_key_location == ApiKeyLocation.QUERY:
                resolved_url = _apply_api_key_query(resolved_url, key_name, key_value)

    return Request(
        method=request.method,
        url=resolved_url,
        http_version=request.http_version,
        headers=resolved_headers,
        body=resolved_body,
        name=request.name,
        environment_variables=request.environment_variables,
        assertions=request.assertions,
        before_hook=request.before_hook,
        after_hook=request.after_hook,
        auth=request.auth,
        multipart=request.multipart,
        span=request.span,
    )
