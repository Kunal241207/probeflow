"""Tests for the CLI interface."""

from __future__ import annotations

import re
import textwrap

import respx
from typer.testing import CliRunner

from probeflow.cli import app

runner = CliRunner()


def strip_ansi(text: str) -> str:
    """Remove ANSI SGR sequences from CLI output before making assertions."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class TestRunCommand:
    @respx.mock
    def test_run_simple_get(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text("GET https://api.example.com/users\n")
        respx.get("https://api.example.com/users").respond(
            json=[{"id": 1, "name": "Alice"}], status_code=200
        )

        result = runner.invoke(app, ["run", str(http_file)])
        assert result.exit_code == 0
        assert "200" in result.stdout

    @respx.mock
    def test_run_with_index(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text(
            textwrap.dedent("""\
            GET https://api.example.com/users

            ###

            POST https://api.example.com/users
            Content-Type: application/json

            {"name": "Bob"}
        """)
        )
        respx.get("https://api.example.com/users").respond(json=[{"id": 1}], status_code=200)

        result = runner.invoke(app, ["run", str(http_file), "--index", "0"])
        assert result.exit_code == 0

    @respx.mock
    def test_run_with_env(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text("GET {{base_url}}/users\n")
        env_file = tmp_path / ".env.dev"
        env_file.write_text("base_url=https://api.example.com\n")
        respx.get("https://api.example.com/users").respond(json=[], status_code=200)

        result = runner.invoke(app, ["run", str(http_file), "--env", "dev"])
        assert result.exit_code == 0

    def test_run_nonexistent_file(self):
        result = runner.invoke(app, ["run", "/tmp/nonexistent.http"])
        assert result.exit_code != 0

    @respx.mock
    def test_run_connection_error(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text("GET https://nonexistent.invalid.test/api\n")
        result = runner.invoke(app, ["run", str(http_file)])
        assert result.exit_code == 1

    @respx.mock
    def test_run_quiet_mode(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text("GET https://api.example.com/users\n")
        respx.get("https://api.example.com/users").respond(json=[], status_code=200)

        result = runner.invoke(app, ["run", str(http_file), "--quiet"])
        assert result.exit_code == 0
        assert "HTTP/1.1" not in result.stdout

    @respx.mock
    def test_run_resolve_failure_sets_exit_code_1(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text("GET https://api.example.com/{{unresolved_var}}\n")
        respx.get("https://api.example.com/").respond(json=[], status_code=200)
        result = runner.invoke(app, ["run", str(http_file)])
        assert result.exit_code == 1


class TestTestCommand:
    @respx.mock
    def test_test_directory_aggregates_files_and_failures(self, tmp_path):
        collection = tmp_path / "requests"
        collection.mkdir()
        nested = collection / "nested"
        nested.mkdir()
        (collection / "a.http").write_text(
            "GET https://api.example.com/a\n### @assert\n# status == 200\n"
        )
        (nested / "b.http").write_text(
            "GET https://api.example.com/b\n### @assert\n# status == 201\n"
        )
        respx.get("https://api.example.com/a").respond(status_code=200)
        respx.get("https://api.example.com/b").respond(status_code=200)

        result = runner.invoke(app, ["test", str(collection)])
        output = strip_ansi(result.stdout + result.stderr)

        assert result.exit_code == 1
        assert "a.http::request-1" in output
        assert "nested/b.http::request-1" in output
        assert "1 passed, 1 failed across 2 file(s)" in output

    @respx.mock
    def test_test_passes_assertions_and_writes_reports(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text(
            textwrap.dedent("""\
            ### @name = getUser
            GET https://api.example.com/users/1

            ### @assert
            # status == 200
            # body.$.name == "Alice"
        """)
        )
        json_file = tmp_path / "results.json"
        junit_file = tmp_path / "results.xml"
        respx.get("https://api.example.com/users/1").respond(
            json={"name": "Alice"}, status_code=200
        )

        result = runner.invoke(
            app,
            ["test", str(http_file), "--json", str(json_file), "--junit-xml", str(junit_file)],
        )

        assert result.exit_code == 0
        assert "PASS" in result.stdout
        assert json_file.exists()
        assert '"passed": true' in json_file.read_text()
        assert '<testsuite tests="1" failures="0"' in junit_file.read_text()

    @respx.mock
    def test_test_fails_assertion(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text(
            textwrap.dedent("""\
            GET https://api.example.com/users/1

            ### @assert
            # status == 201
        """)
        )
        respx.get("https://api.example.com/users/1").respond(status_code=200)

        result = runner.invoke(app, ["test", str(http_file)])
        assert result.exit_code == 1
        output = strip_ansi(result.stdout + result.stderr)
        assert "FAIL" in output
        assert "-201" in output
        assert "+200" in output

    @respx.mock
    def test_test_resolves_response_chain_in_order(self, tmp_path):
        http_file = tmp_path / "chain.http"
        http_file.write_text(
            textwrap.dedent("""\
            ### @name = login
            POST https://api.example.com/login
            Content-Type: application/json

            {"username": "alice"}

            ###

            ### @name = profile
            GET https://api.example.com/me
            Authorization: Bearer {{login.response.body.$.token}}
        """)
        )
        respx.post("https://api.example.com/login").respond(
            json={"token": "abc123"}, status_code=200
        )
        profile_route = respx.get("https://api.example.com/me").respond(
            json={"name": "Alice"}, status_code=200
        )

        result = runner.invoke(app, ["test", str(http_file)])
        assert result.exit_code == 0
        assert profile_route.calls.last.request.headers["Authorization"] == "Bearer abc123"

    def test_test_refuses_hooks_without_executing_them(self, tmp_path):
        http_file = tmp_path / "hooks.http"
        http_file.write_text(
            textwrap.dedent("""\
            ### @before = hooks.py:sign_request
            GET https://api.example.com/users
        """)
        )

        result = runner.invoke(app, ["test", str(http_file)])
        assert result.exit_code == 1
        assert "arbitrary code" in result.stderr

    def test_test_empty_directory(self, tmp_path):
        empty_dir = tmp_path / "empty_dir"
        empty_dir.mkdir()
        result = runner.invoke(app, ["test", str(empty_dir)])
        assert result.exit_code == 1
        output = strip_ansi(result.stdout + result.stderr)
        assert "No .http files found under" in output
        assert "0 passed, 1 failed across 0 file(s)" in output

    @respx.mock
    def test_test_directory_with_syntax_error_file_and_valid_file(self, tmp_path):
        collection = tmp_path / "requests"
        collection.mkdir()
        (collection / "01_valid.http").write_text(
            "GET https://api.example.com/valid\n### @assert\n# status == 200\n"
        )
        (collection / "02_broken.http").write_text(
            "INVALID_METHOD https://api.example.com/broken\n"
        )
        respx.get("https://api.example.com/valid").respond(status_code=200)

        result = runner.invoke(app, ["test", str(collection)])
        assert result.exit_code == 1
        output = strip_ansi(result.stdout + result.stderr)
        assert "01_valid.http::request-1" in output
        assert "PASS" in output
        assert "02_broken.http::parse" in output
        assert "FAIL" in output
        assert "1 passed, 1 failed across 2 file(s)" in output

    @respx.mock
    def test_missing_environment_continues_and_reports(self, tmp_path, monkeypatch):
        import probeflow.test_runner as test_runner
        from probeflow.environment import Environment, EnvironmentNotFoundError

        collection = tmp_path / "requests"
        ok_dir = collection / "ok"
        bad_dir = collection / "bad"
        ok_dir.mkdir(parents=True)
        bad_dir.mkdir(parents=True)
        (ok_dir / "ok.http").write_text(
            "GET https://api.example.com/ok\n### @assert\n# status == 200\n"
        )
        (bad_dir / "needs-env.http").write_text("GET https://api.example.com/env\n")
        respx.get("https://api.example.com/ok").respond(status_code=200)

        def fake_load_environment(directory, env_name=None):
            if directory == bad_dir:
                raise EnvironmentNotFoundError(
                    f"Environment file not found: {directory / '.env.ci'}"
                )
            return Environment(name="default", variables={})

        monkeypatch.setattr(test_runner, "load_environment", fake_load_environment)

        json_file = tmp_path / "results.json"
        junit_file = tmp_path / "results.xml"
        result = runner.invoke(
            app,
            ["test", str(collection), "--json", str(json_file), "--junit-xml", str(junit_file)],
        )
        assert result.exit_code == 1
        output = strip_ansi(result.stdout + result.stderr)
        assert "ok/ok.http::request-1" in output
        assert "PASS" in output
        assert "bad/needs-env.http::environment" in output
        assert "FAIL" in output
        assert "Environment file not found" in output

        json_text = json_file.read_text()
        assert '"passed": false' in json_text
        assert "needs-env.http" in json_text
        assert "Environment file not found" in json_text
        junit_text = junit_file.read_text()
        assert 'classname="probeflow.bad/needs-env.http"' in junit_text
        assert "Environment file not found" in junit_text

    @respx.mock
    def test_test_multiple_failed_assertions_diff(self, tmp_path):
        http_file = tmp_path / "multi_assert.http"
        http_file.write_text(
            textwrap.dedent("""\
            GET https://api.example.com/users/1

            ### @assert
            # status == 201
            # header.Content-Type == "application/xml"
        """)
        )
        respx.get("https://api.example.com/users/1").respond(
            status_code=200,
            headers={"Content-Type": "application/json"},
        )

        result = runner.invoke(app, ["test", str(http_file)])
        assert result.exit_code == 1
        output = strip_ansi(result.stdout + result.stderr)
        assert "FAIL" in output
        assert "-201" in output
        assert "+200" in output
        assert '-"application/xml"' in output
        assert '+"application/json"' in output


class TestValidateCommand:
    def test_validate_valid_file(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text("GET https://api.example.com/users\n")
        result = runner.invoke(app, ["validate", str(http_file)])
        assert result.exit_code == 0
        assert "valid" in result.stdout

    def test_validate_with_env(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text(
            textwrap.dedent("""\
            ### @env = dev
            GET {{base_url}}/users
        """)
        )

        env_file = tmp_path / ".env.dev"
        env_file.write_text("base_url=https://api.example.com\n")
        result = runner.invoke(app, ["validate", str(http_file)])
        assert result.exit_code == 0
        assert "valid" in result.stdout

    def test_validate_unresolved_variables(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text("GET {{unknown_var}}/users\n")
        result = runner.invoke(app, ["validate", str(http_file)])
        assert result.exit_code == 0
        assert "unresolved" in result.stdout.lower() or "{{unknown_var}}" in result.stdout

    def test_validate_unresolved_oauth2_and_multipart_fields(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text(
            "### @oauth2 = client-credentials {{token_url}} {{client_id}}"
            " {{client_secret}} {{scope_read}}\n"
            "### @form = {{form_name}}={{form_value}}\n"
            "### @file = {{file_name}}={{file_path}};type={{file_type}}\n"
            "POST {{upload_url}}\n"
            "Authorization: {{header_value}}\n"
        )
        result = runner.invoke(app, ["validate", str(http_file)])
        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        for var in (
            "token_url",
            "client_id",
            "client_secret",
            "scope_read",
            "form_name",
            "form_value",
            "file_name",
            "file_path",
            "file_type",
            "upload_url",
            "header_value",
        ):
            assert f"{{{{{var}}}}}" in output

    def test_validate_unresolved_chaining_references(self, tmp_path):
        # Response-chaining references ({{name.response.*}}) resolve at runtime
        # from a preceding named request (spec §8/§9). They are valid, not
        # "unresolved", and must NOT be flagged by `validate` -- matching the
        # `test` runner, which skips them via find_unresolved_variables.
        http_file = tmp_path / "test.http"
        http_file.write_text(
            "GET https://api.example.com/users\n"
            "Authorization: Bearer {{login.response.body.$.token}}\n"
            "X-Request-Id: {{login.response.headers.X-Request-Id}}\n"
            "X-Status: {{login.response.status}}\n"
        )
        result = runner.invoke(app, ["validate", str(http_file)])
        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "valid" in output
        # None of the chaining references should be reported as unresolved.
        assert "Unresolved variables" not in output
        for var in (
            "login.response.body.$.token",
            "login.response.headers.X-Request-Id",
            "login.response.status",
        ):
            assert f"{{{{{var}}}}}" not in output

    def test_validate_nonexistent_file(self):
        result = runner.invoke(app, ["validate", "/tmp/nonexistent.http"])
        assert result.exit_code != 0


class TestFormatCommand:
    def test_format_in_place(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text(
            textwrap.dedent("""\
            ### @name = getUsers
            get https://api.example.com/users
            authorization: bearer token123
        """)
        )

        result = runner.invoke(app, ["format", str(http_file)])
        assert result.exit_code == 0
        assert "GET" in http_file.read_text()

    def test_format_check_already_formatted(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text("GET https://api.example.com/users\n")
        result = runner.invoke(app, ["format", str(http_file), "--check"])
        assert result.exit_code == 0
        assert "formatted" in result.stdout.lower()

    def test_format_output_file(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text("### @name = test\nget https://example.com\n")
        output_file = tmp_path / "formatted.http"
        result = runner.invoke(app, ["format", str(http_file), "-o", str(output_file)])
        assert result.exit_code == 0
        assert output_file.exists()


class TestVersionAndHelp:
    def test_version_output(self):
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "probeflow" in result.stdout
        assert "0.2.0" in result.stdout

    def test_main_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "probeflow" in output
        assert "--no-color" in output

    def test_run_help(self):
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "--env" in output
        assert "--index" in output
        assert "--headers" in output


class TestRunEdgeCases:
    """Additional edge-case tests for the run command."""

    def test_run_out_of_range_index(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text("GET https://api.example.com/users\n")

        result = runner.invoke(app, ["run", str(http_file), "--index", "99"])
        assert result.exit_code == 1
        assert "out of range" in result.stderr or "out of range" in result.stdout

    @respx.mock
    def test_run_response_chaining(self, tmp_path):
        """run command should pass responses dict so chaining works."""
        http_file = tmp_path / "chain.http"
        http_file.write_text(
            textwrap.dedent("""\
            ### @name = login
            POST https://api.example.com/login
            Content-Type: application/json

            {"username": "alice"}

            ###

            ### @name = profile
            GET https://api.example.com/me
            Authorization: Bearer {{login.response.body.$.token}}
        """)
        )
        respx.post("https://api.example.com/login").respond(
            json={"token": "tok-xyz"}, status_code=200
        )
        profile_route = respx.get("https://api.example.com/me").respond(
            json={"name": "Alice"}, status_code=200
        )

        result = runner.invoke(app, ["run", str(http_file)])

        assert result.exit_code == 0
        assert profile_route.calls.last.request.headers["Authorization"] == "Bearer tok-xyz"

    @respx.mock
    def test_run_show_headers(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text("GET https://api.example.com/users\n")
        respx.get("https://api.example.com/users").respond(
            json=[],
            status_code=200,
            headers={"X-Custom": "my-value"},
        )

        result = runner.invoke(app, ["run", str(http_file), "--headers"])
        assert result.exit_code == 0
        assert "X-Custom" in result.stdout or "x-custom" in result.stdout.lower()
