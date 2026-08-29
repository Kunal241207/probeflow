"""Environment variable handling and template substitution for .http files."""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from probeflow.models import (
    ApiKeyLocation,
    AuthConfig,
    AuthScheme,
    Environment,
    Header,
    MultipartPart,
    OAuth2ClientCredentials,
    Request,
)

_VARIABLE_PATTERN = re.compile(r"\{\{\s*([^{}\s]+)\s*\}\}")
_DEFAULT_ENV_FILES = [".env", ".env.local"]
_ENV_FILE_PATTERN = ".env.{}"


class EnvironmentNotFoundError(Exception):
    """Raised when a requested environment file cannot be found."""


class VariableResolutionError(Exception):
    """Raised when a response-chaining reference cannot be resolved."""


def _resolve_response_reference(reference: str, responses: dict) -> str:
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
            (v for k, v in response.headers.items() if k.lower() == header_name.lower()),
            None,
        )
    elif field.startswith("body."):
        from probeflow.evaluator import _extract_jsonpath

        value = (
            None
            if not response.json_parsed
            else _extract_jsonpath(response.parsed_body, field.removeprefix("body."))
        )
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
    if not filepath.exists():
        raise EnvironmentNotFoundError(f"Environment file not found: {filepath}")

    from dotenv import dotenv_values

    return {k: v for k, v in dotenv_values(filepath).items() if v is not None}


def find_env_file(
    directory: Path,
    env_name: str | None = None,
) -> Path | None:
    """Search for .env file in directory and all parent directories.

    Named files (e.g. .env.dev) always take precedence over default files
    (.env, .env.local) across all ancestor directories.
    """
    current = directory.resolve()

    if env_name:
        search_dir = current
        while True:
            specific = search_dir / _ENV_FILE_PATTERN.format(env_name)
            if specific.exists():
                return specific
            parent = search_dir.parent
            if parent == search_dir:
                break
            search_dir = parent

    # named files exhausted; fall back to defaults, nearest ancestor first
    search_dir = current
    while True:
        for default in _DEFAULT_ENV_FILES:
            candidate = search_dir / default
            if candidate.exists():
                return candidate
        parent = search_dir.parent
        if parent == search_dir:
            break
        search_dir = parent

    return None


def load_environment(
    directory: Path,
    env_name: str | None = None,
) -> Environment | None:
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
    """Replace {{variable}} placeholders in a string."""

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
    auth: AuthConfig,
    variables: dict[str, str],
    resolved_headers: list[Header],
) -> list[Header]:
    """Apply auth config to the resolved headers list, returning a new list."""
    has_auth_header = any(h.name.lower() == "authorization" for h in resolved_headers)
    if has_auth_header:
        raise VariableResolutionError(
            "Auth config would override an explicit Authorization header. "
            "Remove the Authorization header or the @auth directive."
        )

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


def find_unresolved_variables(
    text: str,
    variables: dict[str, str],
    responses: dict | None = None,
) -> list[str]:
    """Find all unresolved variable references in a string."""
    unresolved: list[str] = []

    def _check(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if ".response." in var_name:
            # Response references are validated at resolution time
            return match.group(0)
        if var_name in variables:
            return match.group(0)
        if os.environ.get(var_name) is not None:
            return match.group(0)
        unresolved.append(var_name)
        return match.group(0)

    _VARIABLE_PATTERN.sub(_check, text)
    return unresolved


def resolve_request(
    request: Request,
    variables: dict[str, str],
    responses: dict | None = None,
) -> Request:
    """Resolve all variable placeholders in a request."""
    from probeflow.models import Header, RequestBody

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

    resolved_auth: AuthConfig | None = None
    if request.auth:
        auth = request.auth
        resolved_auth = AuthConfig(
            scheme=auth.scheme,
            token=substitute_variables(auth.token, variables, responses) if auth.token else None,
            username=(
                substitute_variables(auth.username, variables, responses) if auth.username else None
            ),
            password=(
                substitute_variables(auth.password, variables, responses) if auth.password else None
            ),
            api_key_location=auth.api_key_location,
            api_key_name=(
                substitute_variables(auth.api_key_name, variables, responses)
                if auth.api_key_name
                else None
            ),
            api_key_value=(
                substitute_variables(auth.api_key_value, variables, responses)
                if auth.api_key_value
                else None
            ),
            span=auth.span,
        )

    if resolved_auth:
        resolved_headers = _apply_auth(resolved_auth, variables, resolved_headers)
        if resolved_auth.scheme == AuthScheme.API_KEY:
            if resolved_auth.api_key_location == ApiKeyLocation.QUERY:
                resolved_url = _apply_api_key_query(
                    resolved_url,
                    resolved_auth.api_key_name or "",
                    resolved_auth.api_key_value or "",
                )
            elif resolved_auth.api_key_location == ApiKeyLocation.HEADER:
                resolved_headers.append(
                    Header(
                        name=resolved_auth.api_key_name or "X-API-Key",
                        value=resolved_auth.api_key_value or "",
                    )
                )

    resolved_oauth2: OAuth2ClientCredentials | None = None
    if request.oauth2:
        resolved_oauth2 = OAuth2ClientCredentials(
            token_url=substitute_variables(request.oauth2.token_url, variables, responses),
            client_id=substitute_variables(request.oauth2.client_id, variables, responses),
            client_secret=substitute_variables(request.oauth2.client_secret, variables, responses),
            scopes=[
                substitute_variables(scope, variables, responses) for scope in request.oauth2.scopes
            ],
            span=request.oauth2.span,
        )

    resolved_multipart = [
        MultipartPart(
            name=substitute_variables(part.name, variables, responses),
            value=(
                substitute_variables(part.value, variables, responses)
                if part.value is not None
                else None
            ),
            file_path=(
                substitute_variables(part.file_path, variables, responses)
                if part.file_path is not None
                else None
            ),
            content_type=(
                substitute_variables(part.content_type, variables, responses)
                if part.content_type is not None
                else None
            ),
            span=part.span,
        )
        for part in request.multipart
    ]

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
        auth=resolved_auth,
        oauth2=resolved_oauth2,
        multipart=resolved_multipart,
        span=request.span,
    )
