"""Tests for repo-wide ``probeflow.toml`` configuration and CLI precedence.

Two concerns are covered:

* :func:`probeflow.config.load_config` parsing, validation, and upward
  discovery (unit level).
* The CLI wiring that makes an explicit ``--timeout`` / ``--env`` flag override
  ``probeflow.toml``, which in turn overrides the built-in defaults.

Every test writes its ``probeflow.toml`` under ``tmp_path`` and targets a file
there, so discovery never reaches the repository's own ``probeflow.toml``.
"""

from __future__ import annotations

import textwrap

import pytest
import respx
from typer.testing import CliRunner

from probeflow.cli import app
from probeflow.config import ConfigError, ProbeflowConfig, find_config_file, load_config

runner = CliRunner()


def _write(path, text: str) -> None:
    path.write_text(textwrap.dedent(text), encoding="utf-8")


# ── load_config: parsing and validation ─────────────────────────────────────


class TestLoadConfig:
    def test_no_file_returns_empty(self, tmp_path):
        config = load_config(tmp_path / "requests.http")
        assert config == ProbeflowConfig()
        assert config.timeout is None
        assert config.default_env is None
        assert config.source_path is None

    def test_bracketed_table(self, tmp_path):
        _write(tmp_path / "probeflow.toml", '[probeflow]\ntimeout = 60\nenv = "dev"\n')
        config = load_config(tmp_path / "requests.http")
        assert config.timeout == 60.0
        assert config.default_env == "dev"
        assert config.source_path == (tmp_path / "probeflow.toml").resolve()

    def test_bare_top_level_keys(self, tmp_path):
        _write(tmp_path / "probeflow.toml", 'timeout = 45\nenv = "staging"\n')
        config = load_config(tmp_path / "requests.http")
        assert config.timeout == 45.0
        assert config.default_env == "staging"

    def test_float_timeout(self, tmp_path):
        _write(tmp_path / "probeflow.toml", "[probeflow]\ntimeout = 12.5\n")
        assert load_config(tmp_path).timeout == 12.5

    def test_default_env_alias(self, tmp_path):
        _write(tmp_path / "probeflow.toml", '[probeflow]\ndefault_env = "qa"\n')
        assert load_config(tmp_path).default_env == "qa"

    def test_env_preferred_over_default_env(self, tmp_path):
        _write(
            tmp_path / "probeflow.toml",
            '[probeflow]\nenv = "primary"\ndefault_env = "secondary"\n',
        )
        assert load_config(tmp_path).default_env == "primary"

    def test_timeout_must_be_number(self, tmp_path):
        _write(tmp_path / "probeflow.toml", '[probeflow]\ntimeout = "slow"\n')
        with pytest.raises(ConfigError, match="timeout"):
            load_config(tmp_path)

    def test_timeout_rejects_bool(self, tmp_path):
        _write(tmp_path / "probeflow.toml", "[probeflow]\ntimeout = true\n")
        with pytest.raises(ConfigError, match="timeout"):
            load_config(tmp_path)

    def test_timeout_rejects_nan(self, tmp_path):
        _write(tmp_path / "probeflow.toml", "[probeflow]\ntimeout = nan\n")
        with pytest.raises(ConfigError, match="finite"):
            load_config(tmp_path)

    def test_timeout_rejects_inf(self, tmp_path):
        _write(tmp_path / "probeflow.toml", "[probeflow]\ntimeout = inf\n")
        with pytest.raises(ConfigError, match="finite"):
            load_config(tmp_path)

    def test_timeout_below_minimum(self, tmp_path):
        _write(tmp_path / "probeflow.toml", "[probeflow]\ntimeout = 0.5\n")
        with pytest.raises(ConfigError, match="must be >= 1"):
            load_config(tmp_path)

    def test_env_must_be_string(self, tmp_path):
        _write(tmp_path / "probeflow.toml", "[probeflow]\nenv = 123\n")
        with pytest.raises(ConfigError, match="env"):
            load_config(tmp_path)

    def test_env_must_not_be_empty(self, tmp_path):
        _write(tmp_path / "probeflow.toml", '[probeflow]\nenv = "   "\n')
        with pytest.raises(ConfigError, match="empty"):
            load_config(tmp_path)

    def test_malformed_toml(self, tmp_path):
        _write(tmp_path / "probeflow.toml", "[probeflow]\ntimeout = = =\n")
        with pytest.raises(ConfigError):
            load_config(tmp_path)

    def test_probeflow_key_not_a_table(self, tmp_path):
        _write(tmp_path / "probeflow.toml", 'probeflow = "oops"\n')
        with pytest.raises(ConfigError, match="table"):
            load_config(tmp_path)

    def test_empty_file_is_valid(self, tmp_path):
        _write(tmp_path / "probeflow.toml", "")
        assert load_config(tmp_path) == ProbeflowConfig(
            source_path=(tmp_path / "probeflow.toml").resolve()
        )


# ── find_config_file: upward discovery ──────────────────────────────────────


class TestDiscovery:
    def test_walks_upward_to_find_config(self, tmp_path):
        _write(tmp_path / "probeflow.toml", "[probeflow]\ntimeout = 20\n")
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        found = find_config_file(deep / "requests.http")
        assert found == (tmp_path / "probeflow.toml").resolve()
        assert load_config(deep / "requests.http").timeout == 20.0

    def test_nearest_config_wins(self, tmp_path):
        _write(tmp_path / "probeflow.toml", "[probeflow]\ntimeout = 10\n")
        sub = tmp_path / "sub"
        sub.mkdir()
        _write(sub / "probeflow.toml", "[probeflow]\ntimeout = 25\n")
        assert load_config(sub / "requests.http").timeout == 25.0

    def test_directory_target(self, tmp_path):
        _write(tmp_path / "probeflow.toml", '[probeflow]\nenv = "dir-env"\n')
        # Passing a directory (as `probeflow test <dir>` does) resolves relative
        # to the directory itself, not its parent.
        assert load_config(tmp_path).default_env == "dir-env"

    def test_no_config_anywhere(self, tmp_path):
        assert find_config_file(tmp_path / "requests.http") is None


# ── CLI precedence: flag > probeflow.toml > built-in default ────────────────


class TestCliEnvPrecedence:
    @respx.mock
    def test_config_env_is_used_when_no_flag(self, tmp_path):
        _write(tmp_path / "probeflow.toml", '[probeflow]\nenv = "dev"\n')
        _write(tmp_path / ".env.dev", "base_url=https://dev.example.com\n")
        _write(tmp_path / "req.http", "GET {{base_url}}/users\n")
        route = respx.get("https://dev.example.com/users").respond(json=[], status_code=200)

        result = runner.invoke(app, ["run", str(tmp_path / "req.http")])
        assert result.exit_code == 0, result.stdout
        assert route.called

    @respx.mock
    def test_cli_env_overrides_config(self, tmp_path):
        _write(tmp_path / "probeflow.toml", '[probeflow]\nenv = "dev"\n')
        _write(tmp_path / ".env.dev", "base_url=https://dev.example.com\n")
        _write(tmp_path / ".env.prod", "base_url=https://prod.example.com\n")
        _write(tmp_path / "req.http", "GET {{base_url}}/users\n")
        dev = respx.get("https://dev.example.com/users").respond(json=[], status_code=200)
        prod = respx.get("https://prod.example.com/users").respond(json=[], status_code=200)

        result = runner.invoke(app, ["run", str(tmp_path / "req.http"), "--env", "prod"])
        assert result.exit_code == 0, result.stdout
        assert prod.called
        assert not dev.called

    @respx.mock
    def test_file_env_directive_used_when_config_env_absent(self, tmp_path):
        # Config sets only timeout; the file's own ### @env must still apply.
        _write(tmp_path / "probeflow.toml", "[probeflow]\ntimeout = 15\n")
        _write(tmp_path / ".env.staging", "base_url=https://staging.example.com\n")
        _write(
            tmp_path / "req.http",
            """\
            ### @env = staging

            GET {{base_url}}/users
            """,
        )
        route = respx.get("https://staging.example.com/users").respond(json=[], status_code=200)

        result = runner.invoke(app, ["run", str(tmp_path / "req.http")])
        assert result.exit_code == 0, result.stdout
        assert route.called


class TestCliTimeoutPrecedence:
    def test_config_timeout_passed_to_run(self, tmp_path, monkeypatch):
        _write(tmp_path / "probeflow.toml", "[probeflow]\ntimeout = 7\n")
        _write(tmp_path / "req.http", "GET https://api.example.com/users\n")

        captured = {}

        def fake_execute(request, *, timeout, token_provider, base_dir):
            captured["timeout"] = timeout
            return object()

        monkeypatch.setattr("probeflow.cli.execute_request", fake_execute)
        result = runner.invoke(app, ["run", str(tmp_path / "req.http"), "--quiet"])
        assert result.exit_code == 0, result.stdout
        assert captured["timeout"] == 7.0

    def test_cli_timeout_overrides_config_in_run(self, tmp_path, monkeypatch):
        _write(tmp_path / "probeflow.toml", "[probeflow]\ntimeout = 7\n")
        _write(tmp_path / "req.http", "GET https://api.example.com/users\n")

        captured = {}

        def fake_execute(request, *, timeout, token_provider, base_dir):
            captured["timeout"] = timeout
            return object()

        monkeypatch.setattr("probeflow.cli.execute_request", fake_execute)
        result = runner.invoke(
            app, ["run", str(tmp_path / "req.http"), "--quiet", "--timeout", "15"]
        )
        assert result.exit_code == 0, result.stdout
        assert captured["timeout"] == 15.0

    def test_builtin_default_when_no_config_and_no_flag(self, tmp_path, monkeypatch):
        _write(tmp_path / "req.http", "GET https://api.example.com/users\n")

        captured = {}

        def fake_execute(request, *, timeout, token_provider, base_dir):
            captured["timeout"] = timeout
            return object()

        monkeypatch.setattr("probeflow.cli.execute_request", fake_execute)
        result = runner.invoke(app, ["run", str(tmp_path / "req.http"), "--quiet"])
        assert result.exit_code == 0, result.stdout
        assert captured["timeout"] == 30.0


class TestCliTestCommandPrecedence:
    def test_config_values_passed_to_test_runner(self, tmp_path, monkeypatch):
        _write(tmp_path / "probeflow.toml", '[probeflow]\ntimeout = 9\nenv = "stg"\n')
        _write(tmp_path / "req.http", "GET https://api.example.com/users\n")

        captured = {}

        def fake_run_target(target, *, env_name, timeout):
            captured["env_name"] = env_name
            captured["timeout"] = timeout
            return ([], 0)

        monkeypatch.setattr("probeflow.cli.run_test_target", fake_run_target)
        result = runner.invoke(app, ["test", str(tmp_path / "req.http")])
        assert result.exit_code == 0, result.stdout
        assert captured["timeout"] == 9.0
        assert captured["env_name"] == "stg"

    def test_cli_flags_override_config_in_test(self, tmp_path, monkeypatch):
        _write(tmp_path / "probeflow.toml", '[probeflow]\ntimeout = 9\nenv = "stg"\n')
        _write(tmp_path / "req.http", "GET https://api.example.com/users\n")

        captured = {}

        def fake_run_target(target, *, env_name, timeout):
            captured["env_name"] = env_name
            captured["timeout"] = timeout
            return ([], 0)

        monkeypatch.setattr("probeflow.cli.run_test_target", fake_run_target)
        result = runner.invoke(
            app,
            ["test", str(tmp_path / "req.http"), "--timeout", "3", "--env", "prod"],
        )
        assert result.exit_code == 0, result.stdout
        assert captured["timeout"] == 3.0
        assert captured["env_name"] == "prod"


class TestCliConfigErrors:
    @respx.mock
    def test_malformed_config_exits_with_error(self, tmp_path):
        _write(tmp_path / "probeflow.toml", "[probeflow]\ntimeout = = =\n")
        _write(tmp_path / "req.http", "GET https://api.example.com/users\n")

        result = runner.invoke(app, ["run", str(tmp_path / "req.http")])
        assert result.exit_code == 1

    def test_invalid_timeout_type_exits_in_test(self, tmp_path):
        _write(tmp_path / "probeflow.toml", '[probeflow]\ntimeout = "nope"\n')
        _write(tmp_path / "req.http", "GET https://api.example.com/users\n")

        result = runner.invoke(app, ["test", str(tmp_path / "req.http")])
        assert result.exit_code == 1
