"""Tests for the HTTP client."""

from __future__ import annotations

import httpx
import pytest
import respx

from probeflow.client import OAuth2Error, OAuth2TokenProvider, _status_text, execute_request
from probeflow.models import (
    Header,
    HTTPMethod,
    MultipartPart,
    OAuth2ClientCredentials,
    Request,
    RequestBody,
)


class TestExecuteRequest:
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
        respx.delete("https://api.example.com/users/1").respond(status_code=204)
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
        respx.get("https://api.example.com/final").respond(json={}, status_code=200)
        request = Request(method=HTTPMethod.GET, url="https://api.example.com/final")
        response = execute_request(request)
        assert "final" in response.url

    def test_connection_error(self):
        request = Request(method=HTTPMethod.GET, url="https://nonexistent.invalid.test/api")
        with pytest.raises(ConnectionError):
            execute_request(request, timeout=2.0)

    @respx.mock
    def test_oauth2_reuses_token_within_a_run(self):
        token_route = respx.post("https://auth.example.com/token").respond(
            json={"access_token": "first", "token_type": "Bearer", "expires_in": 3600}
        )
        api_route = respx.get("https://api.example.com/me").respond(status_code=200)
        request = Request(
            method=HTTPMethod.GET,
            url="https://api.example.com/me",
            oauth2=OAuth2ClientCredentials(
                token_url="https://auth.example.com/token",
                client_id="client",
                client_secret="secret",
                scopes=["read"],
            ),
        )
        provider = OAuth2TokenProvider()

        execute_request(request, token_provider=provider)
        execute_request(request, token_provider=provider)

        assert token_route.call_count == 1
        assert api_route.calls[0].request.headers["Authorization"] == "Bearer first"
        assert api_route.calls[1].request.headers["Authorization"] == "Bearer first"

    @respx.mock
    def test_oauth2_refreshes_before_expiry(self):
        now = [0.0]
        token_route = respx.post("https://auth.example.com/token").mock(
            side_effect=[
                httpx.Response(200, json={"access_token": "first", "expires_in": 31}),
                httpx.Response(200, json={"access_token": "second", "expires_in": 3600}),
            ]
        )
        api_route = respx.get("https://api.example.com/me").respond(status_code=200)
        request = Request(
            method=HTTPMethod.GET,
            url="https://api.example.com/me",
            oauth2=OAuth2ClientCredentials(
                token_url="https://auth.example.com/token",
                client_id="client",
                client_secret="secret",
            ),
        )
        provider = OAuth2TokenProvider(clock=lambda: now[0])

        execute_request(request, token_provider=provider)
        now[0] = 2.0
        execute_request(request, token_provider=provider)

        assert token_route.call_count == 2
        assert api_route.calls[1].request.headers["Authorization"] == "Bearer second"

    @respx.mock
    def test_oauth2_token_request_sends_form_urlencoded(self):
        """RFC 6749 requires application/x-www-form-urlencoded for client-credentials."""
        token_route = respx.post("https://auth.example.com/token").respond(
            json={"access_token": "test", "token_type": "Bearer", "expires_in": 3600}
        )
        _api_route = respx.get("https://api.example.com/me").respond(status_code=200)
        request = Request(
            method=HTTPMethod.GET,
            url="https://api.example.com/me",
            oauth2=OAuth2ClientCredentials(
                token_url="https://auth.example.com/token",
                client_id="client",
                client_secret="secret",
                scopes=["read", "write"],
            ),
        )
        execute_request(request)

        sent_request = token_route.calls[0].request
        assert sent_request.headers["Content-Type"] == "application/x-www-form-urlencoded"
        # Verify the body contains the expected form fields
        body = sent_request.content.decode()
        assert "grant_type=client_credentials" in body
        assert "scope=read+write" in body

    @respx.mock
    def test_multipart_upload_uses_a_generated_boundary(self, tmp_path):
        upload = tmp_path / "avatar.txt"
        upload.write_text("hello upload", encoding="utf-8")
        route = respx.post("https://api.example.com/avatar").respond(status_code=201)
        request = Request(
            method=HTTPMethod.POST,
            url="https://api.example.com/avatar",
            multipart=[
                MultipartPart(name="display_name", value="Ada"),
                MultipartPart(name="avatar", file_path="avatar.txt", content_type="text/plain"),
            ],
        )

        response = execute_request(request, base_dir=tmp_path)
        assert response.status_code == 201
        sent = route.calls[0].request
        assert sent.headers["Content-Type"].startswith("multipart/form-data; boundary=")
        assert b'name="display_name"' in sent.content
        assert b"Ada" in sent.content
        assert b'filename="avatar.txt"' in sent.content
        assert b"hello upload" in sent.content

    def test_multipart_rejects_explicit_content_type(self):
        request = Request(
            method=HTTPMethod.POST,
            url="https://api.example.com/avatar",
            headers=[Header(name="Content-Type", value="multipart/form-data")],
            multipart=[MultipartPart(name="display_name", value="Ada")],
        )
        with pytest.raises(ValueError, match="cannot set Content-Type explicitly"):
            execute_request(request)

    def test_multipart_and_body_is_rejected(self):
        request = Request(
            method=HTTPMethod.POST,
            url="https://api.example.com/avatar",
            body=RequestBody(content='{"name": "Ada"}', content_type="application/json"),
            multipart=[MultipartPart(name="display_name", value="Ada")],
        )
        with pytest.raises(ValueError, match="cannot also set a request body"):
            execute_request(request)

    def test_multipart_missing_file_is_an_error(self, tmp_path):
        request = Request(
            method=HTTPMethod.POST,
            url="https://api.example.com/avatar",
            multipart=[MultipartPart(name="avatar", file_path="missing.png")],
        )
        with pytest.raises(FileNotFoundError, match="Multipart file not found"):
            execute_request(request, base_dir=tmp_path)

    def test_multipart_path_traversal_is_rejected(self, tmp_path):
        sub_dir = tmp_path / "subdir"
        sub_dir.mkdir()
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("secret_content")

        request = Request(
            method=HTTPMethod.POST,
            url="https://api.example.com/upload",
            multipart=[MultipartPart(name="file", file_path="../secret.txt")],
        )
        with pytest.raises(ValueError, match="attempts traversal outside base directory"):
            execute_request(request, base_dir=sub_dir)

    def test_multipart_absolute_path_outside_base_dir_is_rejected(self, tmp_path):
        sub_dir = tmp_path / "subdir"
        sub_dir.mkdir()
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("secret_content")

        request = Request(
            method=HTTPMethod.POST,
            url="https://api.example.com/upload",
            multipart=[MultipartPart(name="file", file_path=str(secret_file.resolve()))],
        )
        with pytest.raises(ValueError, match="attempts traversal outside base directory"):
            execute_request(request, base_dir=sub_dir)

    @respx.mock
    def test_multipart_zero_byte_file(self, tmp_path):
        empty_file = tmp_path / "empty.dat"
        empty_file.write_bytes(b"")
        route = respx.post("https://api.example.com/upload").respond(status_code=200)

        request = Request(
            method=HTTPMethod.POST,
            url="https://api.example.com/upload",
            multipart=[MultipartPart(name="file", file_path="empty.dat")],
        )
        response = execute_request(request, base_dir=tmp_path)
        assert response.status_code == 200
        assert route.called

    @respx.mock
    def test_multipart_large_file(self, tmp_path):
        large_file = tmp_path / "large.bin"
        large_file.write_bytes(b"x" * (1024 * 1024))
        route = respx.post("https://api.example.com/upload").respond(status_code=200)

        request = Request(
            method=HTTPMethod.POST,
            url="https://api.example.com/upload",
            multipart=[MultipartPart(name="file", file_path="large.bin")],
        )
        response = execute_request(request, base_dir=tmp_path)
        assert response.status_code == 200
        assert route.called

    @respx.mock
    def test_oauth2_token_endpoint_http_error(self):
        respx.post("https://auth.example.com/token").respond(
            status_code=401, json={"error": "invalid_client"}
        )
        request = Request(
            method=HTTPMethod.GET,
            url="https://api.example.com/me",
            oauth2=OAuth2ClientCredentials(
                token_url="https://auth.example.com/token",
                client_id="client",
                client_secret="secret",
            ),
        )
        with pytest.raises(OAuth2Error, match="OAuth2 token request failed"):
            execute_request(request)

    @respx.mock
    def test_oauth2_token_endpoint_malformed_json(self):
        respx.post("https://auth.example.com/token").respond(
            status_code=200, content="<html>Not JSON</html>", headers={"content-type": "text/html"}
        )
        request = Request(
            method=HTTPMethod.GET,
            url="https://api.example.com/me",
            oauth2=OAuth2ClientCredentials(
                token_url="https://auth.example.com/token",
                client_id="client",
                client_secret="secret",
            ),
        )
        with pytest.raises(OAuth2Error, match="not valid JSON"):
            execute_request(request)

    @respx.mock
    def test_oauth2_token_endpoint_missing_access_token(self):
        respx.post("https://auth.example.com/token").respond(
            status_code=200, json={"token_type": "Bearer", "expires_in": 3600}
        )
        request = Request(
            method=HTTPMethod.GET,
            url="https://api.example.com/me",
            oauth2=OAuth2ClientCredentials(
                token_url="https://auth.example.com/token",
                client_id="client",
                client_secret="secret",
            ),
        )
        with pytest.raises(OAuth2Error, match="did not include access_token"):
            execute_request(request)

    @respx.mock
    def test_oauth2_token_endpoint_timeout(self):
        respx.post("https://auth.example.com/token").mock(
            side_effect=httpx.ConnectTimeout("Connection timed out")
        )
        request = Request(
            method=HTTPMethod.GET,
            url="https://api.example.com/me",
            oauth2=OAuth2ClientCredentials(
                token_url="https://auth.example.com/token",
                client_id="client",
                client_secret="secret",
            ),
        )
        with pytest.raises(OAuth2Error, match="OAuth2 token request failed"):
            execute_request(request)


class TestStatusText:
    def test_common_codes(self):
        assert _status_text(200) == "OK"
        assert _status_text(201) == "Created"
        assert _status_text(404) == "Not Found"
        assert _status_text(500) == "Internal Server Error"

    def test_unknown_code(self):
        assert "199" in _status_text(199)
        assert "451" in _status_text(451)
