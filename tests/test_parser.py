"""Tests for the .http file parser."""

from __future__ import annotations

import textwrap

import pytest

from probeflow.models import HTTPMethod, ParseError
from probeflow.parser import format_request, parse_string


class TestBasicParsing:
    """Tests for parsing basic request structures."""

    def test_simple_get_request(self):
        content = "GET https://httpbin.org/get"
        result = parse_string(content)
        assert len(result.requests) == 1
        req = result.requests[0]
        assert req.method == HTTPMethod.GET
        assert req.url == "https://httpbin.org/get"
        assert req.headers == []
        assert req.body is None

    def test_http_version_is_preserved(self):
        result = parse_string("GET https://httpbin.org/get HTTP/1.1")
        assert result.requests[0].http_version == "HTTP/1.1"
        assert "HTTP/1.1" in format_request(result.requests[0])

    def test_post_with_json_body(self):
        content = textwrap.dedent("""\
            POST https://httpbin.org/post
            Content-Type: application/json
            Accept: application/json

            {"name": "test", "value": 42}
        """)
        result = parse_string(content)
        req = result.requests[0]
        assert req.method == HTTPMethod.POST
        assert req.url == "https://httpbin.org/post"
        assert len(req.headers) == 2
        assert req.headers[0].name == "Content-Type"
        assert req.headers[0].value == "application/json"
        assert req.headers[1].name == "Accept"
        assert req.headers[1].value == "application/json"
        assert req.body is not None
        assert req.body.content == '{"name": "test", "value": 42}'
        assert req.body.content_type == "application/json"

    def test_all_http_methods(self):
        methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
        for method in methods:
            content = f"{method} https://httpbin.org/{method.lower()}"
            result = parse_string(content)
            assert len(result.requests) == 1
            assert result.requests[0].method == HTTPMethod(method)

    def test_method_case_insensitive(self):
        content = "get https://httpbin.org/get"
        result = parse_string(content)
        assert result.requests[0].method == HTTPMethod.GET

    def test_request_with_http_version(self):
        content = "GET https://httpbin.org/get HTTP/1.1"
        result = parse_string(content)
        assert result.requests[0].url == "https://httpbin.org/get"

    def test_empty_headers_body(self):
        content = "GET https://httpbin.org/get\n\n"
        result = parse_string(content)
        assert result.requests[0].headers == []
        assert result.requests[0].body is None


class TestMultipleRequests:
    """Tests for parsing files with multiple requests."""

    def test_two_requests_separated(self):
        content = textwrap.dedent("""\
            GET https://httpbin.org/get

            ###

            POST https://httpbin.org/post
            Content-Type: application/json

            {"test": true}
        """)
        result = parse_string(content)
        assert len(result.requests) == 2
        assert result.requests[0].method == HTTPMethod.GET
        assert result.requests[1].method == HTTPMethod.POST

    def test_named_requests(self):
        content = textwrap.dedent("""\
            ### @name = getUsers
            GET https://api.example.com/users

            ###

            ### @name = createUser
            POST https://api.example.com/users
            Content-Type: application/json

            {"name": "Alice"}
        """)
        result = parse_string(content)
        assert len(result.requests) == 2
        assert result.requests[0].name == "getUsers"
        assert result.requests[1].name == "createUser"


class TestComments:
    """Tests for comment handling."""

    def test_inline_comments_skipped(self):
        content = textwrap.dedent("""\
            GET https://httpbin.org/get
            // This is a comment
            Accept: application/json
        """)
        result = parse_string(content)
        assert len(result.requests[0].headers) == 1
        assert result.requests[0].headers[0].name == "Accept"

    def test_hash_comments_skipped(self):
        content = textwrap.dedent("""\
            GET https://httpbin.org/get
            # This is also a comment
            Accept: text/plain
        """)
        result = parse_string(content)
        assert len(result.requests[0].headers) == 1


class TestHeaders:
    """Tests for header parsing."""

    def test_multiple_headers(self):
        content = textwrap.dedent("""\
            GET https://httpbin.org/get
            Authorization: Bearer token123
            Accept: application/json
            X-Custom-Header: custom-value
        """)
        result = parse_string(content)
        assert len(result.requests[0].headers) == 3

    def test_content_type_detection(self):
        content = textwrap.dedent("""\
            POST https://httpbin.org/post
            Content-Type: application/json

            {"key": "value"}
        """)
        result = parse_string(content)
        assert result.requests[0].body is not None
        assert result.requests[0].body.content_type == "application/json"

    def test_header_with_semicolon_content_type(self):
        content = textwrap.dedent("""\
            POST https://httpbin.org/post
            Content-Type: application/json; charset=utf-8

            {"key": "value"}
        """)
        result = parse_string(content)
        assert result.requests[0].body.content_type == "application/json"


class TestBodyParsing:
    """Tests for request body parsing."""

    def test_json_body(self):
        content = textwrap.dedent("""\
            POST https://httpbin.org/post
            Content-Type: application/json

            {
                "name": "test",
                "nested": {
                    "key": "value"
                }
            }
        """)
        result = parse_string(content)
        body = result.requests[0].body
        assert body is not None
        parsed = body.parsed
        assert isinstance(parsed, dict)
        assert parsed["name"] == "test"
        assert parsed["nested"]["key"] == "value"

    def test_plain_text_body(self):
        content = textwrap.dedent("""\
            POST https://httpbin.org/post
            Content-Type: text/plain

            Hello, world!
        """)
        result = parse_string(content)
        body = result.requests[0].body
        assert body is not None
        assert body.parsed == "Hello, world!"

    def test_multiline_body(self):
        content = textwrap.dedent("""\
            POST https://httpbin.org/post
            Content-Type: text/plain

            Line 1
            Line 2
            Line 3
        """)
        result = parse_string(content)
        body = result.requests[0].body
        assert body is not None
        assert "Line 1" in body.content
        assert "Line 2" in body.content
        assert "Line 3" in body.content


class TestEnvironment:
    """Tests for environment variable detection."""

    def test_env_name_detection(self):
        content = textwrap.dedent("""\
            ### @env = dev
            GET https://httpbin.org/get
        """)
        result = parse_string(content)
        assert result.environment_name == "dev"

    def test_inline_variable_definitions(self):
        """Unknown ### @key = value directives are now skipped by the parser.

        The new spec-driven parser only recognizes known directives
        (@name, @env, @before, @after, @assert). Arbitrary @key = value
        pairs are no longer extracted as inline environment variables.
        Variables should come from .env files or system env instead.
        """
        content = textwrap.dedent("""\
            ### @name = testReq
            GET {{baseUrl}}/users
            Authorization: Bearer {{token}}
        """)
        result = parse_string(content)
        req = result.requests[0]
        assert req.name == "testReq"
        # Variables come from .env files, not inline directives
        assert req.url == "{{baseUrl}}/users"


class TestErrorHandling:
    """Tests for parser error handling."""

    def test_invalid_method(self):
        content = "INVALID https://httpbin.org/get"
        with pytest.raises(ParseError, match="Unsupported HTTP method"):
            parse_string(content)

    def test_empty_file(self):
        result = parse_string("")
        assert len(result.requests) == 0

    def test_comments_only_file(self):
        content = textwrap.dedent("""\
            // Just a comment
            # Another comment
        """)
        result = parse_string(content)
        assert len(result.requests) == 0


class TestFormatRequest:
    """Tests for formatting requests back to .http syntax."""

    def test_roundtrip_simple_get(self):
        request_text = "GET https://httpbin.org/get"
        result = parse_string(request_text)
        formatted = format_request(result.requests[0])
        assert "GET https://httpbin.org/get" in formatted

    def test_roundtrip_named_request(self):
        content = textwrap.dedent("""\
            ### @name = testRequest
            GET https://httpbin.org/get
        """)
        result = parse_string(content)
        formatted = format_request(result.requests[0])
        assert "@name = testRequest" in formatted

    def test_format_with_headers(self):
        content = textwrap.dedent("""\
            POST https://httpbin.org/post
            Content-Type: application/json
            Accept: application/json

            {"key": "value"}
        """)
        result = parse_string(content)
        formatted = format_request(result.requests[0])
        assert "Content-Type: application/json" in formatted
        assert "Accept: application/json" in formatted

    def test_format_without_body(self):
        content = "GET https://httpbin.org/get"
        result = parse_string(content)
        formatted = format_request(result.requests[0])
        assert formatted.strip() == "GET https://httpbin.org/get"

    def test_format_with_hooks(self):
        content = textwrap.dedent("""\
            ### @before = f.py:b
            GET https://httpbin.org/get

            ### @after = f.py:a
        """)
        result = parse_string(content)
        formatted = format_request(result.requests[0])
        assert "### @before = f.py:b" in formatted
        assert "### @after = f.py:a" in formatted


class TestParserEdgeCases:
    """Tests for specific edge cases and error messages to improve coverage."""

    def test_invalid_hook_reference(self):
        content = textwrap.dedent("""\
            ### @before = not_a_hook
            GET https://httpbin.org/get
        """)
        with pytest.raises(ParseError, match="Invalid hook reference"):
            parse_string(content)

    def test_orphaned_assert(self):
        content = "### @assert"
        result = parse_string(content)
        assert len(result.requests) == 0

    def test_invalid_assertion_syntax(self):
        content = textwrap.dedent("""\
            GET https://httpbin.org/get

            ### @assert
            # unknown_prop == 123
        """)
        with pytest.raises(ParseError, match="Invalid assertion syntax"):
            parse_string(content)

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            from probeflow.parser import parse_file

            parse_file("does_not_exist.http")

    def test_literal_parsing_edge_cases(self):
        # Assertions go AFTER the request line per the EBNF grammar
        # float
        result = parse_string("GET https://a.com\n\n### @assert\n# status == 2.5")
        assert result.requests[0].assertions.assertions[0].expected == 2.5
        # int fallback
        result = parse_string("GET https://a.com\n\n### @assert\n# status == -42")
        assert result.requests[0].assertions.assertions[0].expected == -42
        # string fallback
        result = parse_string("GET https://a.com\n\n### @assert\n# status == something_weird")
        assert result.requests[0].assertions.assertions[0].expected == "something_weird"

    def test_duration_assertion(self):
        result = parse_string("GET https://a.com\n\n### @assert\n# duration < 100ms")
        assert result.requests[0].assertions.assertions[0].expected == 100
        assert result.requests[0].assertions.assertions[0].target == "duration"

    def test_invalid_duration_assertion(self):
        with pytest.raises(ParseError, match="Invalid assertion"):
            parse_string("GET https://a.com\n\n### @assert\n# duration = 100")

    def test_orphaned_file_level_directives(self):
        content = textwrap.dedent("""\
            ### @after = f:x
            ### @assert
            ### @unknown = x
            GET https://httpbin.org/get
        """)
        result = parse_string(content)
        # Should parse the GET request, skipping the orphaned file-level directives
        assert len(result.requests) == 1
        assert result.requests[0].after_hook is None

    def test_name_and_after_in_correct_positions(self):
        """@name is pre-request, @after is post-request."""
        content = textwrap.dedent("""\
            ### @name = myReq
            GET https://httpbin.org/get

            ### @after = hooks.py:on_done
        """)
        result = parse_string(content)
        assert result.requests[0].name == "myReq"
        assert result.requests[0].after_hook is not None
        assert result.requests[0].after_hook.function_name == "on_done"

    def test_body_with_missing_newline(self):
        content = "GET https://a.com\n\nbody"
        result = parse_string(content)
        assert result.requests[0].body.content == "body"

    def test_boolean_literals_in_assertions(self):
        """Cover true/false parsing in _parse_value_literal (lines 206, 208)."""
        result = parse_string("GET https://a.com\n\n### @assert\n# body.$.active == true")
        assert result.requests[0].assertions.assertions[0].expected is True

        result = parse_string("GET https://a.com\n\n### @assert\n# body.$.active == false")
        assert result.requests[0].assertions.assertions[0].expected is False

    def test_null_literal_in_assertion(self):
        """Cover null parsing (line 212)."""
        result = parse_string("GET https://a.com\n\n### @assert\n# body.$.deleted == null")
        assert result.requests[0].assertions.assertions[0].expected is None

    def test_quoted_string_in_assertion(self):
        """Cover quoted string parsing (line 202)."""
        result = parse_string('GET https://a.com\n\n### @assert\n# body.$.name == "hello"')
        assert result.requests[0].assertions.assertions[0].expected == "hello"

    def test_list_literal_in_assertion(self):
        """Cover list parsing (lines 215-220)."""
        result = parse_string("GET https://a.com\n\n### @assert\n# status in [200, 201]")
        assertion = result.requests[0].assertions.assertions[0]
        assert assertion.expected == [200, 201]

    def test_empty_list_in_assertion(self):
        """Cover empty list (line 218)."""
        result = parse_string("GET https://a.com\n\n### @assert\n# body.$.items in []")
        assert result.requests[0].assertions.assertions[0].expected == []

    def test_slash_comment_in_assert_block(self):
        """Cover // comments inside assert blocks (lines 645-648)."""
        content = textwrap.dedent("""\
            GET https://a.com

            ### @assert
            // This is a double-slash comment inside the assert block
            # status == 200
        """)
        result = parse_string(content)
        assert len(result.requests[0].assertions.assertions) == 1

    def test_orphaned_header_at_file_level(self):
        """Cover orphaned HEADER token at top level (lines 415-417)."""
        content = "X-Orphaned: value\n\nGET https://a.com"
        result = parse_string(content)
        # The orphaned header is skipped, only the GET request is parsed
        assert len(result.requests) == 1
        assert len(result.requests[0].headers) == 0

    def test_separator_in_pre_request_meta(self):
        """Cover SEPARATOR token in pre-request metadata loop (lines 483-484)."""
        content = textwrap.dedent("""\
            ### @name = myReq
            ###
            GET https://a.com
        """)
        result = parse_string(content)
        assert result.requests[0].name == "myReq"

    def test_env_inside_request_block(self):
        """Cover @env inside a request block (lines 473-476)."""
        content = textwrap.dedent("""\
            ### @env = staging
            ### @name = myReq
            GET https://a.com
        """)
        result = parse_string(content)
        assert result.environment_name == "staging"
        assert result.requests[0].name == "myReq"

    def test_generic_error_non_alpha_body_line(self):
        """Cover the generic error path in _error_invalid_request_line (line 769)."""
        content = "123 not-a-method-not-alpha"
        with pytest.raises(ParseError, match="Unexpected content"):
            parse_string(content)

    def test_format_with_assertions_roundtrip(self):
        """Cover assertions formatting (lines 857-861)."""
        content = textwrap.dedent("""\
            GET https://a.com

            ### @assert
            # status == 200
            # body.$.name == "test"
        """)
        result = parse_string(content)
        formatted = format_request(result.requests[0])
        assert "### @assert" in formatted
        assert "# status == 200" in formatted

    def test_header_contains_assertions_and_after_hook(self):
        """Cover assert+after in post-request metadata."""
        content = textwrap.dedent("""\
            GET https://a.com

            ### @assert
            # status == 200

            ### @after = hooks.py:cleanup
        """)
        result = parse_string(content)
        req = result.requests[0]
        assert req.assertions is not None
        assert len(req.assertions.assertions) == 1
        assert req.after_hook is not None
        assert req.after_hook.function_name == "cleanup"
