"""Tests for Phase 2 features: auth helpers, multipart, response chaining."""

from __future__ import annotations

import base64

import pytest
import respx

from probeflow.client import execute_request
from probeflow.environment import VariableResolutionError, resolve_request
from probeflow.models import (
    ApiKeyLocation,
    AuthConfig,
    AuthScheme,
    Header,
    HTTPMethod,
    MultipartPart,
    Request,
)


class TestAuthHelpers:
    def test_bearer_token_added_as_authorization_header(self):
        request = Request(
            method=HTTPMethod.GET,
            url="https://api.example.com/me",
            auth=AuthConfig(scheme=AuthScheme.BEARER, token="token-123"),
        )
        resolved = resolve_request(request, {})
        assert resolved.headers[-1].name == "Authorization"
        assert resolved.headers[-1].value == "Bearer token-123"

    def test_bearer_token_variable_substitution(self):
        request = Request(
            method=HTTPMethod.GET,
            url="https://api.example.com/me",
            auth=AuthConfig(scheme=AuthScheme.BEARER, token="{{TOKEN}}"),
        )
        resolved = resolve_request(request, {"TOKEN": "abc123"})
        assert resolved.headers[-1].value == "Bearer abc123"

    def test_basic_auth_encodes_credentials(self):
        request = Request(
            method=HTTPMethod.GET,
            url="https://api.example.com/me",
            auth=AuthConfig(scheme=AuthScheme.BASIC, username="alice", password="secret"),
        )
        resolved = resolve_request(request, {})
        expected = "Basic " + base64.b64encode(b"alice:secret").decode()
        assert resolved.headers[-1].value == expected

    def test_api_key_query_param_appended(self):
        request = Request(
            method=HTTPMethod.GET,
            url="https://api.example.com/search?q=test",
            auth=AuthConfig(
                scheme=AuthScheme.API_KEY,
                api_key_location=ApiKeyLocation.QUERY,
                api_key_name="api_key",
                api_key_value="key-456",
            ),
        )
        resolved = resolve_request(request, {})
        assert "api_key=key-456" in resolved.url
        assert "q=test" in resolved.url

    def test_api_key_header_injected(self):
        request = Request(
            method=HTTPMethod.GET,
            url="https://api.example.com/data",
            auth=AuthConfig(
                scheme=AuthScheme.API_KEY,
                api_key_location=ApiKeyLocation.HEADER,
                api_key_name="X-API-Key",
                api_key_value="key-789",
            ),
        )
        resolved = resolve_request(request, {})
        header_names = [h.name for h in resolved.headers]
        assert "X-API-Key" in header_names

    def test_auth_does_not_silently_override_explicit_header(self):
        request = Request(
            method=HTTPMethod.GET,
            url="https://api.example.com",
            headers=[Header(name="Authorization", value="Bearer explicit")],
            auth=AuthConfig(scheme=AuthScheme.BEARER, token="secret"),
        )
        with pytest.raises(VariableResolutionError, match="override"):
            resolve_request(request, {})


class TestMultipart:
    @respx.mock
    def test_client_sends_multipart_form_fields(self, tmp_path):
        route = respx.post("https://api.example.com/upload").respond(status_code=201)
        request = Request(
            method=HTTPMethod.POST,
            url="https://api.example.com/upload",
            multipart=[
                MultipartPart(name="title", value="Quarterly report"),
                MultipartPart(name="author", value="Alice"),
            ],
        )
        response = execute_request(request)
        assert response.status_code == 201
        sent = route.calls.last.request
        assert sent.headers["content-type"].startswith("multipart/form-data; boundary=")
        assert b"Quarterly report" in sent.content

    @respx.mock
    def test_client_sends_multipart_with_file(self, tmp_path):
        payload = tmp_path / "report.txt"
        payload.write_text("phase two", encoding="utf-8")
        route = respx.post("https://api.example.com/upload").respond(status_code=201)
        request = Request(
            method=HTTPMethod.POST,
            url="https://api.example.com/upload",
            multipart=[
                MultipartPart(name="title", value="Quarterly report"),
                MultipartPart(name="attachment", file_path="report.txt", content_type="text/plain"),
            ],
        )
        response = execute_request(request, base_dir=tmp_path)
        assert response.status_code == 201
        sent = route.calls.last.request
        assert b"Quarterly report" in sent.content
        assert b"phase two" in sent.content
