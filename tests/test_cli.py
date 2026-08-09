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
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


class TestRunCommand:
    """Tests for the `probeflow run` command."""

    @respx.mock
    def test_run_simple_get(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text("GET https://api.example.com/users\n")

        respx.get("https://api.example.com/users").respond(
            json=[{"id": 1, "name": "Alice"}],
            status_code=200,
        )

        result = runner.invoke(app, ["run", str(http_file)])
        assert result.exit_code == 0
        assert "200" in result.stdout

    @respx.mock
    def test_run_with_index(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text(textwrap.dedent("""\
            GET https://api.example.com/users

            ###

            POST https://api.example.com/users
            Content-Type: application/json

            {"name": "Bob"}
        """))

        respx.get("https://api.example.com/users").respond(
            json=[{"id": 1}],
            status_code=200,
        )

        result = runner.invoke(app, ["run", str(http_file), "--index", "0"])
        assert result.exit_code == 0

    @respx.mock
    def test_run_with_env(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text("GET {{base_url}}/users\n")

        env_file = tmp_path / ".env.dev"
        env_file.write_text("base_url=https://api.example.com\n")

        respx.get("https://api.example.com/users").respond(
            json=[],
            status_code=200,
        )

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

        respx.get("https://api.example.com/users").respond(
            json=[],
            status_code=200,
        )

        result = runner.invoke(app, ["run", str(http_file), "--quiet"])
        assert result.exit_code == 0
        # In quiet mode, no output should appear
        assert "HTTP/1.1" not in result.stdout


class TestTestCommand:
    """Tests for the enforceable API test loop."""

    @respx.mock
    def test_test_passes_assertions_and_writes_reports(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text(textwrap.dedent("""\
            ### @name = getUser
            GET https://api.example.com/users/1

            ### @assert
            # status == 200
            # body.$.name == "Alice"
        """))
        json_file = tmp_path / "results.json"
        junit_file = tmp_path / "results.xml"

        respx.get("https://api.example.com/users/1").respond(
            json={"name": "Alice"}, status_code=200
        )

        result = runner.invoke(
            app,
            [
                "test",
                str(http_file),
                "--json",
                str(json_file),
                "--junit-xml",
                str(junit_file),
            ],
        )

        assert result.exit_code == 0
        assert "PASS" in result.stdout
        assert json_file.exists()
        assert '"passed": true' in json_file.read_text()
        assert '<testsuite tests="1" failures="0"' in junit_file.read_text()

    @respx.mock
    def test_test_fails_assertion(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text(textwrap.dedent("""\
            GET https://api.example.com/users/1

            ### @assert
            # status == 201
        """))
        respx.get("https://api.example.com/users/1").respond(status_code=200)

        result = runner.invoke(app, ["test", str(http_file)])

        assert result.exit_code == 1
        assert "FAIL" in result.stderr

    @respx.mock
    def test_test_resolves_response_chain_in_order(self, tmp_path):
        http_file = tmp_path / "chain.http"
        http_file.write_text(textwrap.dedent("""\
            ### @name = login
            POST https://api.example.com/login
            Content-Type: application/json

            {"username": "alice"}

            ###

            ### @name = profile
            GET https://api.example.com/me
            Authorization: Bearer {{login.response.body.$.token}}
        """))
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
        http_file.write_text(textwrap.dedent("""\
            ### @before = hooks.py:sign_request
            GET https://api.example.com/users
        """))

        result = runner.invoke(app, ["test", str(http_file)])

        assert result.exit_code == 1
        assert "arbitrary code" in result.stderr


class TestValidateCommand:
    """Tests for the `probeflow validate` command."""

    def test_validate_valid_file(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text("GET https://api.example.com/users\n")

        result = runner.invoke(app, ["validate", str(http_file)])
        assert result.exit_code == 0
        assert "valid" in result.stdout

    def test_validate_with_env(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text(textwrap.dedent("""\
            ### @env = dev
            GET {{base_url}}/users
        """))

        env_file = tmp_path / ".env.dev"
        env_file.write_text("base_url=https://api.example.com\n")

        result = runner.invoke(app, ["validate", str(http_file)])
        assert result.exit_code == 0
        assert "valid" in result.stdout

    def test_validate_unresolved_variables(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text("GET {{unknown_var}}/users\n")

        result = runner.invoke(app, ["validate", str(http_file)])
        assert result.exit_code == 0  # Still valid syntax, just has unresolved vars
        assert "unresolved" in result.stdout.lower() or "{{unknown_var}}" in result.stdout

    def test_validate_nonexistent_file(self):
        result = runner.invoke(app, ["validate", "/tmp/nonexistent.http"])
        assert result.exit_code != 0


class TestFormatCommand:
    """Tests for the `probeflow format` command."""

    def test_format_in_place(self, tmp_path):
        http_file = tmp_path / "test.http"
        http_file.write_text(textwrap.dedent("""\
            ### @name = getUsers
            get https://api.example.com/users
            authorization: bearer token123
        """))

        result = runner.invoke(app, ["format", str(http_file)])
        assert result.exit_code == 0

        # Check the file was reformatted
        content = http_file.read_text()
        assert "GET" in content  # Method should be uppercased

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


class TestVersionCommand:
    """Tests for the `probeflow version` command."""

    def test_version_output(self):
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "probeflow" in result.stdout
        assert "0.1.0" in result.stdout


class TestHelpOutput:
    """Tests for help text."""

    def test_main_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "probeflow" in output

    def test_run_help(self):
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "--env" in output
        assert "--index" in output
        assert "--headers" in output
