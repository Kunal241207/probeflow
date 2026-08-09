"""Tests for the HTTP client."""

from __future__ import annotations

import pytest
import respx

from probeflow.client import _status_text, execute_request
from probeflow.models import Header, HTTPMethod, Request, RequestBody


class TestExecuteRequest:
    """Tests for executing HTTP requests."""

    @respx.mock
    def test_simple_get(self):
        respx.get("https://api.example.com/users").respond(
            json=[{"id": 1, "name": "Alice"}],
            status_code=200,
        )
        request = Request(method=HTTPMethod.GET, url="https://api.example.com/users")
        response = execute_request(request)
        assert response.status_code == 200
        assert response.status_text == "OK"
        assert response.parsed_body == [{"id": 1, "name": "Alice"}]
        assert response.elapsed_ms >= 0
        assert response.size_bytes > 0

    @respx.mock
    def test_post_with_json_body(self):
        route = respx.post("https://api.example.com/users").respond(
            json={"id": 1, "name": "Bob"},
            status_code=201,
        )
        request = Request(
            method=HTTPMethod.POST,
            url="https://api.example.com/users",
            headers=[Header(name="Content-Type", value="application/json")],
            body=RequestBody(content='{"name": "Bob"}', content_type="application/json"),
        )
        response = execute_request(request)
        assert response.status_code == 201
        assert response.status_text == "Created"
        assert route.called

    @respx.mock
    def test_put_request(self):
        respx.put("https://api.example.com/users/1").respond(
            json={"id": 1, "name": "Updated"},
            status_code=200,
        )
        request = Request(
            method=HTTPMethod.PUT,
            url="https://api.example.com/users/1",
            body=RequestBody(content='{"name": "Updated"}', content_type="application/json"),
        )
        response = execute_request(request)
        assert response.status_code == 200

    @respx.mock
    def test_delete_request(self):
        respx.delete("https://api.example.com/users/1").respond(
            status_code=204,
        )
        request = Request(method=HTTPMethod.DELETE, url="https://api.example.com/users/1")
        response = execute_request(request)
        assert response.status_code == 204
        assert response.status_text == "No Content"

    @respx.mock
    def test_error_response(self):
        respx.get("https://api.example.com/not-found").respond(
            json={"error": "Not found"},
            status_code=404,
        )
        request = Request(method=HTTPMethod.GET, url="https://api.example.com/not-found")
        response = execute_request(request)
        assert response.status_code == 404
        assert response.status_text == "Not Found"

    @respx.mock
    def test_response_headers_preserved(self):
        respx.get("https://api.example.com/test").respond(
            json={},
            headers={"X-Custom": "value", "Content-Type": "application/json"},
            status_code=200,
        )
        request = Request(method=HTTPMethod.GET, url="https://api.example.com/test")
        response = execute_request(request)
        assert "x-custom" in response.headers
        assert response.headers["x-custom"] == "value"

    @respx.mock
    def test_non_json_response(self):
        respx.get("https://api.example.com/text").respond(
            content="Hello, world!",
            status_code=200,
        )
        request = Request(method=HTTPMethod.GET, url="https://api.example.com/text")
        response = execute_request(request)
        assert response.status_code == 200
        assert response.body == "Hello, world!"
        assert response.parsed_body is None

    @respx.mock
    def test_response_url(self):
        respx.get("https://api.example.com/final").respond(
            json={},
            status_code=200,
        )
        request = Request(method=HTTPMethod.GET, url="https://api.example.com/final")
        response = execute_request(request)
        assert "final" in response.url

    def test_connection_error(self):
        request = Request(method=HTTPMethod.GET, url="https://nonexistent.invalid.test/api")
        with pytest.raises(ConnectionError):
            execute_request(request, timeout=2.0)


class TestStatusText:
    """Tests for status code text lookup."""

    def test_common_codes(self):
        assert _status_text(200) == "OK"
        assert _status_text(201) == "Created"
        assert _status_text(404) == "Not Found"
        assert _status_text(500) == "Internal Server Error"

    def test_unknown_code(self):
        assert "199" in _status_text(199)
        assert "451" in _status_text(451)
