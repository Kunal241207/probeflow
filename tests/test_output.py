"""Tests for deterministic, CI-safe console color policy."""

from __future__ import annotations

from probeflow.formatter import configure_output, should_use_color


class _TTY:
    def isatty(self) -> bool:
        return True


class _Pipe:
    def isatty(self) -> bool:
        return False


def test_color_policy_honors_nonempty_no_color_only(monkeypatch):
    configure_output(False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert should_use_color(_TTY())

    monkeypatch.setenv("NO_COLOR", "")
    assert should_use_color(_TTY())

    monkeypatch.setenv("NO_COLOR", "1")
    assert not should_use_color(_TTY())


def test_color_policy_disables_non_tty_and_explicit_flag(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    configure_output(False)
    assert not should_use_color(_Pipe())

    configure_output(True)
    assert not should_use_color(_TTY())
    configure_output(False)
