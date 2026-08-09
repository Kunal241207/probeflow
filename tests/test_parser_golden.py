"""Golden-file tests for the .http parser.

Loads every .http fixture in tests/fixtures/, parses it, and compares
the result against a snapshot JSON file in tests/fixtures/golden/.

Run with --update-golden to regenerate golden files after intentional
spec changes:
    pytest tests/test_parser_golden.py --update-golden

Also includes round-trip tests: parse → format → parse, asserting
semantic equality for every valid fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from probeflow.models import ParseError
from probeflow.parser import format_file, parse_file, parse_string

FIXTURES_DIR = Path(__file__).parent / "fixtures"
GOLDEN_DIR = FIXTURES_DIR / "golden"

# Fixtures that are expected to parse without errors
VALID_FIXTURES = [
    "simple_get.http",
    "multi_request.http",
    "named_requests.http",
    "headers_and_body.http",
    "env_variables.http",
    "comments_mixed.http",
    "http_version.http",
    "assert_blocks.http",
    "assert_literals.http",
    "chaining_refs.http",
    "hooks.http",
    "vscode_compat.http",
    "jetbrains_compat.http",
    "empty.http",
    "comments_only.http",
]

# Fixtures that are expected to raise ParseError
MALFORMED_FIXTURES = [
    "malformed_method.http",
    "malformed_header.http",
    "malformed_body.http",
]

# Fixtures that parse successfully but contain structural oddities
# (the parser is lenient on these — they're not errors, but edge cases)
LENIENT_FIXTURES = [
    "malformed_unclosed_var.http",
]


def _request_file_to_dict(rf) -> dict:
    """Convert a RequestFile to a JSON-serializable dict for golden comparison.

    Strips SourceSpan data to keep golden files stable and readable.
    """

    def _strip_spans(obj):
        if isinstance(obj, dict):
            return {k: _strip_spans(v) for k, v in obj.items() if k != "span"}
        if isinstance(obj, list):
            return [_strip_spans(item) for item in obj]
        return obj

    return _strip_spans(rf.model_dump(mode="json"))


# ── pytest configuration ──────────────────────────────────────────────────


@pytest.fixture
def update_golden(request):
    return request.config.getoption("--update-golden")


# ── Golden-file snapshot tests ────────────────────────────────────────────


class TestGoldenFiles:
    """Parse each valid fixture and compare against the golden snapshot."""

    @pytest.mark.parametrize("fixture_name", VALID_FIXTURES)
    def test_golden_match(self, fixture_name, update_golden):
        fixture_path = FIXTURES_DIR / fixture_name
        assert fixture_path.exists(), f"Fixture file missing: {fixture_path}"

        result = parse_file(fixture_path)
        actual = _request_file_to_dict(result)

        golden_path = GOLDEN_DIR / fixture_name.replace(".http", ".expected.json")

        if update_golden:
            GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
            golden_path.write_text(
                json.dumps(actual, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            pytest.skip(f"Golden file updated: {golden_path.name}")
            return

        assert golden_path.exists(), (
            f"Golden file missing: {golden_path}. "
            f"Run: pytest tests/test_parser_golden.py --update-golden"
        )

        expected = json.loads(golden_path.read_text(encoding="utf-8"))

        assert actual == expected, (
            f"Parse output differs from golden file for {fixture_name}.\n"
            f"Run: pytest tests/test_parser_golden.py --update-golden\n"
            f"to regenerate."
        )


class TestMalformedFiles:
    """Verify that malformed fixtures produce ParseError with location info."""

    @pytest.mark.parametrize("fixture_name", MALFORMED_FIXTURES)
    def test_malformed_raises_parse_error(self, fixture_name):
        fixture_path = FIXTURES_DIR / fixture_name

        with pytest.raises(ParseError) as exc_info:
            parse_file(fixture_path)

        error = exc_info.value
        # Must have line number
        assert error.line is not None, f"ParseError for {fixture_name} is missing line number"
        # Must have column number
        assert error.column is not None, f"ParseError for {fixture_name} is missing column number"
        # Must have a human-readable message (not a bare traceback)
        assert error.raw_message, f"ParseError for {fixture_name} has no message"

    def test_malformed_method_error_content(self):
        """Verify the specific error for an invalid HTTP method."""
        fixture_path = FIXTURES_DIR / "malformed_method.http"

        with pytest.raises(ParseError, match="Unsupported HTTP method"):
            parse_file(fixture_path)


class TestLenientParsing:
    """Fixtures with structural oddities that the parser handles gracefully."""

    @pytest.mark.parametrize("fixture_name", LENIENT_FIXTURES)
    def test_lenient_parses_without_crash(self, fixture_name):
        """These files may have issues but should not crash the parser."""
        fixture_path = FIXTURES_DIR / fixture_name
        # Should not raise — the parser is lenient on these edge cases
        result = parse_file(fixture_path)
        # Should still return a valid RequestFile
        assert result is not None
        assert result.filename == fixture_name


# ── Round-trip tests ──────────────────────────────────────────────────────


class TestRoundTrip:
    """Parse → format → parse should produce semantically identical results."""

    @pytest.mark.parametrize(
        "fixture_name", [f for f in VALID_FIXTURES if f not in ("empty.http", "comments_only.http")]
    )
    def test_roundtrip_semantic_equality(self, fixture_name):
        """Format output re-parses to the same semantic content."""
        fixture_path = FIXTURES_DIR / fixture_name
        original = parse_file(fixture_path)

        if not original.requests:
            pytest.skip(f"No requests in {fixture_name}")

        # Format the parsed result back to .http text
        formatted_text = format_file(original)

        # Re-parse the formatted text
        reparsed = parse_string(formatted_text, filename=fixture_name)

        # Compare semantics (not spans, not exact text)
        assert len(reparsed.requests) == len(original.requests), (
            f"Round-trip changed request count: {len(original.requests)} → {len(reparsed.requests)}"
        )
        assert reparsed.environment_name == original.environment_name, (
            f"Environment changed from {original.environment_name} to {reparsed.environment_name}"
        )

        for i, (orig_req, re_req) in enumerate(
            zip(original.requests, reparsed.requests, strict=True)
        ):
            assert orig_req.method == re_req.method, (
                f"Request {i}: method changed from {orig_req.method} to {re_req.method}"
            )
            assert orig_req.url == re_req.url, (
                f"Request {i}: URL changed from {orig_req.url} to {re_req.url}"
            )
            assert orig_req.http_version == re_req.http_version, (
                f"Request {i}: HTTP version changed from "
                f"{orig_req.http_version} to {re_req.http_version}"
            )
            assert orig_req.name == re_req.name, (
                f"Request {i}: name changed from {orig_req.name} to {re_req.name}"
            )

            # Compare headers by name+value (ignoring span)
            orig_headers = [(h.name, h.value) for h in orig_req.headers]
            re_headers = [(h.name, h.value) for h in re_req.headers]
            assert orig_headers == re_headers, (
                f"Request {i}: headers changed\n"
                f"  Original: {orig_headers}\n"
                f"  Reparsed: {re_headers}"
            )

            # Compare body content
            if orig_req.body:
                assert re_req.body is not None, f"Request {i}: body lost in round-trip"
                assert orig_req.body.content.strip() == re_req.body.content.strip(), (
                    f"Request {i}: body content changed"
                )
            else:
                assert re_req.body is None, f"Request {i}: body appeared in round-trip"

            # Compare assertions
            if orig_req.assertions:
                assert re_req.assertions is not None, f"Request {i}: assertions lost in round-trip"
                assert len(orig_req.assertions.assertions) == len(re_req.assertions.assertions), (
                    f"Request {i}: assertion count changed"
                )

            # Compare hooks
            if orig_req.before_hook:
                assert re_req.before_hook is not None, (
                    f"Request {i}: before_hook lost in round-trip"
                )
                assert orig_req.before_hook.file_path == re_req.before_hook.file_path
                assert orig_req.before_hook.function_name == re_req.before_hook.function_name

            if orig_req.after_hook:
                assert re_req.after_hook is not None, f"Request {i}: after_hook lost in round-trip"


# ── Error quality tests ──────────────────────────────────────────────────


class TestErrorQuality:
    """Verify that parse errors are human-readable and contain location info."""

    def test_error_has_filename(self):
        """ParseError includes the filename in the message."""
        with pytest.raises(ParseError) as exc_info:
            parse_string("INVALID https://example.com", filename="test.http")

        error = exc_info.value
        assert "test.http" in str(error)

    def test_error_has_line_and_column(self):
        """ParseError includes line and column numbers."""
        with pytest.raises(ParseError) as exc_info:
            parse_string("INVALID https://example.com", filename="test.http")

        error = exc_info.value
        assert error.line is not None
        assert error.column is not None
        assert error.line >= 1
        assert error.column >= 1

    def test_error_message_format(self):
        """Error message follows the file:line:col: error: message format."""
        with pytest.raises(ParseError) as exc_info:
            parse_string("INVALID https://example.com", filename="test.http")

        msg = str(exc_info.value)
        # Should be formatted as "test.http:1:1: error: ..."
        assert "test.http:" in msg
        assert "error:" in msg

    def test_error_no_bare_traceback(self):
        """Errors should be structured, not Python tracebacks."""
        with pytest.raises(ParseError) as exc_info:
            parse_string("INVALID https://example.com")

        msg = str(exc_info.value)
        assert "Traceback" not in msg
        assert "File " not in msg

    def test_multiline_error_points_to_correct_line(self):
        """Error on line 3 should report line=3."""
        content = "GET https://example.com/ok\n\n###\n\nINVALID https://example.com/bad"

        with pytest.raises(ParseError) as exc_info:
            parse_string(content, filename="multi.http")

        error = exc_info.value
        assert error.line == 5  # INVALID is on line 5
