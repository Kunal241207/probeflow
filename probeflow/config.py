"""Repo-wide defaults loaded from ``probeflow.toml``.

Discovered by walking up from the target file to the nearest ``probeflow.toml``.
An explicit CLI flag overrides the file, which overrides the built-in default.
See docs/spec.md §15 for the full format and precedence rules.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_FILENAME = "probeflow.toml"


class ConfigError(Exception):
    """Raised when a ``probeflow.toml`` exists but is malformed or invalid."""


@dataclass
class ProbeflowConfig:
    """Resolved repo-wide defaults. ``None`` means 'unset; fall back'."""

    timeout: float | None = None
    default_env: str | None = None
    source_path: Path | None = None


def find_config_file(start: Path) -> Path | None:
    """Return the nearest ``probeflow.toml`` at or above ``start``, or None."""
    base = start if start.is_dir() else start.parent
    try:
        base = base.resolve()
    except OSError:
        return None
    for directory in [base, *base.parents]:
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def _coerce_timeout(section: dict, path: Path) -> float | None:
    if "timeout" not in section:
        return None
    value = section["timeout"]
    # bool is a subclass of int; reject it explicitly so `timeout = true` errors.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path}: 'timeout' must be a number, got {type(value).__name__}")
    value = float(value)
    if not math.isfinite(value):
        raise ConfigError(f"{path}: 'timeout' must be a finite number, got {value}")
    if value < 1.0:
        raise ConfigError(f"{path}: 'timeout' must be >= 1.0 seconds, got {value}")
    return value


def _coerce_env(section: dict, path: Path) -> str | None:
    for key in ("env", "default_env"):
        if key not in section:
            continue
        value = section[key]
        if value is None:
            return None
        if not isinstance(value, str):
            raise ConfigError(f"{path}: '{key}' must be a string, got {type(value).__name__}")
        if not value.strip():
            raise ConfigError(f"{path}: '{key}' must not be empty")
        return value
    return None


def load_config(start: Path | str | None = None) -> ProbeflowConfig:
    """Load repo-wide defaults for the given target path.

    ``start`` may be a ``.http`` file or a directory; discovery walks upward from
    it. When no ``probeflow.toml`` is found, an empty config is returned (all
    fields ``None``), so callers always fall back to their built-in defaults.
    Raises :class:`ConfigError` when a file exists but is malformed or invalid.
    """
    start_path = Path(start) if start is not None else Path.cwd()
    config_path = find_config_file(start_path)
    if config_path is None:
        return ProbeflowConfig()

    try:
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Failed to read {config_path}: {exc}") from exc

    section = data.get("probeflow", data)
    if not isinstance(section, dict):
        raise ConfigError(f"{config_path}: [probeflow] must be a table")

    return ProbeflowConfig(
        timeout=_coerce_timeout(section, config_path),
        default_env=_coerce_env(section, config_path),
        source_path=config_path,
    )
