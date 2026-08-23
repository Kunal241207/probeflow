"""Tests for environment variable handling."""

from __future__ import annotations

import os

import pytest

from probeflow.client import Response
from probeflow.environment import (
    EnvironmentNotFoundError,
    find_env_file,
    load_env_file,
    load_environment,
    resolve_request,
    substitute_variables,
)
from probeflow.models import (
    AssertBlock,
    Assertion,
    AssertionOperator,
    AssertionTarget,
    Header,
    HookRef,
    HTTPMethod,
    MultipartPart,
    OAuth2ClientCredentials,
    Request,
    RequestBody,
)


class TestSubstituteVariables:
    def test_simple_substitution(self):
        result = substitute_variables(
            "https://{{host}}/api/{{version}}",
            {"host": "api.example.com", "version": "v1"},
        )
        assert result == "https://api.example.com/api/v1"

    def test_no_variables(self):
        assert substitute_variables("https://example.com", {}) == "https://example.com"

    def test_unresolved_variable_preserved(self):
        result = substitute_variables("https://{{unknown}}/test", {"host": "api.example.com"})
        assert result == "https://{{unknown}}/test"

    def test_system_env_fallback(self):
        os.environ["TEST_API_TOKEN"] = "sys-token-123"
        try:
            assert substitute_variables("Bearer {{TEST_API_TOKEN}}", {}) == "Bearer sys-token-123"
        finally:
            del os.environ["TEST_API_TOKEN"]

    def test_whitespace_in_braces(self):
        result = substitute_variables("{{  baseUrl  }}", {"baseUrl": "https://api.example.com"})
        assert result == "https://api.example.com"

    def test_multiple_same_variable(self):
        result = substitute_variables("{{x}} and {{x}} again", {"x": "hello"})
        assert result == "hello and hello again"


class TestLoadEnvFile:
    def test_load_basic_env(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("API_KEY=secret123\nBASE_URL=https://api.example.com\n")
        variables = load_env_file(env_file)
        assert variables["API_KEY"] == "secret123"
        assert variables["BASE_URL"] == "https://api.example.com"

    def test_load_env_with_comments(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# Comment\nAPI_KEY=secret123\n# Another comment\n")
        variables = load_env_file(env_file)
        assert variables["API_KEY"] == "secret123"

    def test_load_env_with_quoted_values(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text('API_KEY="secret with spaces"\nURL="https://example.com"\n')
        variables = load_env_file(env_file)
        assert variables["API_KEY"] == "secret with spaces"
        assert variables["URL"] == "https://example.com"

    def test_missing_env_file_raises(self, tmp_path):
        with pytest.raises(EnvironmentNotFoundError):
            load_env_file(tmp_path / ".env.nonexistent")


class TestFindEnvFile:
    def test_find_default_env(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value\n")
        assert find_env_file(tmp_path) == env_file

    def test_find_named_env(self, tmp_path):
        dev_env = tmp_path / ".env.dev"
        dev_env.write_text("ENV=dev\n")
        assert find_env_file(tmp_path, "dev") == dev_env

    def test_named_env_fallback_to_default(self, tmp_path):
        default_env = tmp_path / ".env"
        default_env.write_text("ENV=default\n")
        assert find_env_file(tmp_path, "staging") == default_env

    def test_no_env_file_found(self, tmp_path):
        assert find_env_file(tmp_path) is None


class TestResolveRequest:
    def test_resolve_url(self):
        request = Request(method=HTTPMethod.GET, url="https://{{host}}/api/users")
        resolved = resolve_request(request, {"host": "api.example.com"})
        assert resolved.url == "https://api.example.com/api/users"

    def test_resolve_headers(self):
        request = Request(
            method=HTTPMethod.GET,
            url="https://example.com",
            headers=[Header(name="Authorization", value="Bearer {{token}}")],
        )
        resolved = resolve_request(request, {"token": "abc123"})
        assert resolved.headers[0].value == "Bearer abc123"

    def test_resolve_body(self):
        request = Request(
            method=HTTPMethod.POST,
            url="https://example.com",
            body=RequestBody(content='{"user": "{{username}}"}', content_type="application/json"),
        )
        resolved = resolve_request(request, {"username": "alice"})
        assert resolved.body is not None
        assert "alice" in resolved.body.content

    def test_resolve_preserves_name(self):
        request = Request(method=HTTPMethod.GET, url="https://{{host}}/test", name="testRequest")
        resolved = resolve_request(request, {"host": "example.com"})
        assert resolved.name == "testRequest"

    def test_resolve_preserves_extensions_and_http_version(self):
        request = Request(
            method=HTTPMethod.GET,
            url="https://example.com",
            http_version="1.1",
            assertions=AssertBlock(
                assertions=[
                    Assertion(
                        target=AssertionTarget.STATUS, operator=AssertionOperator.EQ, expected=200
                    )
                ]
            ),
            before_hook=HookRef(file_path="hooks.py", function_name="before"),
            after_hook=HookRef(file_path="hooks.py", function_name="after"),
        )
        resolved = resolve_request(request, {})
        assert resolved.http_version == "1.1"
        assert resolved.assertions == request.assertions
        assert resolved.before_hook == request.before_hook
        assert resolved.after_hook == request.after_hook

    def test_resolve_response_chain(self):
        response = Response(
            status_code=200,
            status_text="OK",
            elapsed_ms=10.0,
            size_bytes=20,
            headers={"X-Request-Id": "req-123"},
            body='{"token": "abc123"}',
            parsed_body={"token": "abc123"},
        )
        request = Request(
            method=HTTPMethod.GET,
            url="https://example.com/me",
            headers=[
                Header(name="Authorization", value="Bearer {{login.response.body.$.token}}"),
                Header(name="X-Request-Id", value="{{login.response.headers.X-Request-Id}}"),
            ],
        )
        resolved = resolve_request(request, {}, {"login": response})
        assert resolved.headers[0].value == "Bearer abc123"
        assert resolved.headers[1].value == "req-123"

    def test_missing_response_chain_is_an_error(self):
        request = Request(
            method=HTTPMethod.GET, url="https://example.com/me/{{login.response.status}}"
        )
        with pytest.raises(Exception, match="preceding successful request"):
            resolve_request(request, {}, {})

    def test_resolve_oauth2_variables(self):
        request = Request(
            method=HTTPMethod.GET,
            url="https://api.example.com/me",
            oauth2=OAuth2ClientCredentials(
                token_url="{{token_url}}",
                client_id="{{client_id}}",
                client_secret="{{client_secret}}",
                scopes=["{{scope}}"],
            ),
        )
        resolved = resolve_request(
            request,
            {
                "token_url": "https://auth.example.com/token",
                "client_id": "client",
                "client_secret": "secret",
                "scope": "read",
            },
        )
        assert resolved.oauth2 is not None
        assert resolved.oauth2.token_url == "https://auth.example.com/token"
        assert resolved.oauth2.scopes == ["read"]

    def test_resolve_multipart_variables(self):
        request = Request(
            method=HTTPMethod.POST,
            url="https://api.example.com/avatar",
            multipart=[
                MultipartPart(name="{{field}}", value="{{name}}"),
                MultipartPart(name="avatar", file_path="{{asset}}", content_type="{{media_type}}"),
            ],
        )
        resolved = resolve_request(
            request,
            {
                "field": "display_name",
                "name": "Ada",
                "asset": "avatar.png",
                "media_type": "image/png",
            },
        )
        assert resolved.multipart[0].name == "display_name"
        assert resolved.multipart[0].value == "Ada"
        assert resolved.multipart[1].file_path == "avatar.png"
        assert resolved.multipart[1].content_type == "image/png"


class TestLoadEnvironment:
    def test_load_default_environment(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("API_KEY=123\nBASE_URL=https://api.example.com\n")
        env = load_environment(tmp_path)
        assert env is not None
        assert env.name == "default"
        assert env.variables["API_KEY"] == "123"

    def test_load_named_environment(self, tmp_path):
        dev_env = tmp_path / ".env.dev"
        dev_env.write_text("API_KEY=dev-key\n")
        env = load_environment(tmp_path, "dev")
        assert env is not None
        assert env.name == "dev"
        assert env.variables["API_KEY"] == "dev-key"

    def test_no_environment_found(self, tmp_path):
        assert load_environment(tmp_path) is None
