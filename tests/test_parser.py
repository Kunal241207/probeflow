"""Tests for the .http file parser."""

from __future__ import annotations

import textwrap

import pytest

from probeflow.models import HTTPMethod, ParseError
from probeflow.parser import format_request, parse_file, parse_string


class TestBasicParsing:
    def test_simple_get_request(self):
        result = parse_string("GET https://httpbin.org/get")
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
        assert req.body is not None
        assert req.body.content == '{"name": "test", "value": 42}'
        assert req.body.content_type == "application/json"

    def test_all_http_methods(self):
        methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
        for method in methods:
            result = parse_string(f"{method} https://httpbin.org/{method.lower()}")
            assert len(result.requests) == 1
            assert result.requests[0].method == HTTPMethod(method)

    def test_method_case_insensitive(self):
        result = parse_string("get https://httpbin.org/get")
        assert result.requests[0].method == HTTPMethod.GET

    def test_empty_headers_body(self):
        result = parse_string("GET https://httpbin.org/get\n\n")
        assert result.requests[0].headers == []
        assert result.requests[0].body is None


class TestMultipleRequests:
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
        assert body.parsed["name"] == "test"
        assert body.parsed["nested"]["key"] == "value"

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


class TestDirectives:
    def test_env_name_detection(self):
        result = parse_string("### @env = dev\nGET https://httpbin.org/get")
        assert result.environment_name == "dev"

    def test_oauth2_client_credentials_directive(self):
        content = (
            "### @oauth2 = client-credentials {{token_url}} "
            "{{client_id}} {{client_secret}} read write\n"
            "GET https://api.example.com/me\n"
        )
        result = parse_string(content)
        oauth2 = result.requests[0].oauth2
        assert oauth2 is not None
        assert oauth2.token_url == "{{token_url}}"
        assert oauth2.scopes == ["read", "write"]

    def test_multipart_form_and_file_directives(self):
        result = parse_string(
            textwrap.dedent("""\
            ### @form = display_name=Ada
            ### @file = avatar=fixtures/avatar.png;type=image/png
            POST https://api.example.com/avatar
        """)
        )
        parts = result.requests[0].multipart
        assert parts[0].name == "display_name"
        assert parts[0].value == "Ada"
        assert parts[1].file_path == "fixtures/avatar.png"
        assert parts[1].content_type == "image/png"

    def test_multipart_rejects_manual_content_type(self):
        with pytest.raises(ParseError, match="must not set Content-Type"):
            parse_string(
                textwrap.dedent("""\
                ### @form = display_name=Ada
                POST https://api.example.com/avatar
                Content-Type: multipart/form-data
            """)
            )


class TestErrorHandling:
    def test_invalid_method(self):
        with pytest.raises(ParseError, match="Unsupported HTTP method"):
            parse_string("INVALID https://httpbin.org/get")

    def test_empty_file(self):
        assert len(parse_string("").requests) == 0

    def test_comments_only_file(self):
        content = textwrap.dedent("""\
            // Just a comment
            # Another comment
        """)
        assert len(parse_string(content).requests) == 0

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            parse_file("does_not_exist.http")

    def test_invalid_hook_reference(self):
        with pytest.raises(ParseError, match="Invalid hook reference"):
            parse_string("### @before = not_a_hook\nGET https://httpbin.org/get")

    def test_invalid_assertion_syntax(self):
        with pytest.raises(ParseError, match="Invalid assertion syntax"):
            parse_string("GET https://httpbin.org/get\n\n### @assert\n# unknown_prop == 123")

    def test_empty_assert_block(self):
        with pytest.raises(ParseError, match="Empty @assert block"):
            parse_string("GET https://httpbin.org/get\n\n### @assert\n")

    def test_assert_block_with_only_invalid_assertion(self):
        with pytest.raises(ParseError, match="Invalid assertion syntax"):
            parse_string("GET https://httpbin.org/get\n\n### @assert\n# just a comment\n")

    def test_invalid_duration_assertion(self):
        with pytest.raises(ParseError, match="Invalid assertion"):
            parse_string("GET https://a.com\n\n### @assert\n# duration = 100")

    def test_generic_error_non_alpha_body_line(self):
        with pytest.raises(ParseError, match="Unexpected content"):
            parse_string("123 not-a-method-not-alpha")


class TestFormatRequest:
    def test_roundtrip_simple_get(self):
        result = parse_string("GET https://httpbin.org/get")
        assert "GET https://httpbin.org/get" in format_request(result.requests[0])

    def test_roundtrip_named_request(self):
        content = "### @name = testRequest\nGET https://httpbin.org/get"
        result = parse_string(content)
        assert "@name = testRequest" in format_request(result.requests[0])

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
        result = parse_string("GET https://httpbin.org/get")
        assert format_request(result.requests[0]).strip() == "GET https://httpbin.org/get"

    def test_format_with_hooks(self):
        content = "### @before = f.py:b\nGET https://httpbin.org/get\n\n### @after = f.py:a"
        result = parse_string(content)
        formatted = format_request(result.requests[0])
        assert "### @before = f.py:b" in formatted
        assert "### @after = f.py:a" in formatted

    def test_format_with_multipart_parts(self):
        content = textwrap.dedent("""\
            ### @form = display_name=Ada
            ### @file = avatar=fixtures/avatar.png;type=image/png
            POST https://api.example.com/avatar
        """)
        result = parse_string(content)
        formatted = format_request(result.requests[0])
        assert "### @form = display_name=Ada" in formatted
        assert "### @file = avatar=fixtures/avatar.png;type=image/png" in formatted

    def test_format_with_assertions_roundtrip(self):
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


class TestParserEdgeCases:
    def test_orphaned_assert(self):
        result = parse_string("### @assert")
        assert len(result.requests) == 0

    def test_literal_parsing_edge_cases(self):
        result = parse_string("GET https://a.com\n\n### @assert\n# status == 2.5")
        assert result.requests[0].assertions.assertions[0].expected == 2.5

        result = parse_string("GET https://a.com\n\n### @assert\n# status == -42")
        assert result.requests[0].assertions.assertions[0].expected == -42

        result = parse_string("GET https://a.com\n\n### @assert\n# status == something_weird")
        assert result.requests[0].assertions.assertions[0].expected == "something_weird"

    def test_duration_assertion(self):
        result = parse_string("GET https://a.com\n\n### @assert\n# duration < 100ms")
        assert result.requests[0].assertions.assertions[0].expected == 100
        assert result.requests[0].assertions.assertions[0].target == "duration"

    def test_orphaned_file_level_directives(self):
        content = textwrap.dedent("""\
            ### @after = f:x
            ### @assert
            ### @unknown = x
            GET https://httpbin.org/get
        """)
        result = parse_string(content)
        assert len(result.requests) == 1
        assert result.requests[0].after_hook is None

    def test_name_and_after_in_correct_positions(self):
        content = textwrap.dedent("""\
            ### @name = myReq
            GET https://httpbin.org/get

            ### @after = hooks.py:on_done
        """)
        result = parse_string(content)
        assert result.requests[0].name == "myReq"
        assert result.requests[0].after_hook.function_name == "on_done"

    def test_body_with_missing_newline(self):
        result = parse_string("GET https://a.com\n\nbody")
        assert result.requests[0].body.content == "body"

    def test_boolean_literals_in_assertions(self):
        result = parse_string("GET https://a.com\n\n### @assert\n# body.$.active == true")
        assert result.requests[0].assertions.assertions[0].expected is True

        result = parse_string("GET https://a.com\n\n### @assert\n# body.$.active == false")
        assert result.requests[0].assertions.assertions[0].expected is False

    def test_null_literal_in_assertion(self):
        result = parse_string("GET https://a.com\n\n### @assert\n# body.$.deleted == null")
        assert result.requests[0].assertions.assertions[0].expected is None

    def test_quoted_string_in_assertion(self):
        result = parse_string('GET https://a.com\n\n### @assert\n# body.$.name == "hello"')
        assert result.requests[0].assertions.assertions[0].expected == "hello"

    def test_list_literal_in_assertion(self):
        result = parse_string("GET https://a.com\n\n### @assert\n# status in [200, 201]")
        assert result.requests[0].assertions.assertions[0].expected == [200, 201]

    def test_empty_list_in_assertion(self):
        result = parse_string("GET https://a.com\n\n### @assert\n# body.$.items in []")
        assert result.requests[0].assertions.assertions[0].expected == []

    def test_slash_comment_in_assert_block(self):
        content = textwrap.dedent("""\
            GET https://a.com

            ### @assert
            // Comment inside assert block
            # status == 200
        """)
        result = parse_string(content)
        assert len(result.requests[0].assertions.assertions) == 1

    def test_orphaned_header_at_file_level(self):
        result = parse_string("X-Orphaned: value\n\nGET https://a.com")
        assert len(result.requests) == 1
        assert len(result.requests[0].headers) == 0

    def test_separator_in_pre_request_meta(self):
        content = "### @name = myReq\n###\nGET https://a.com"
        result = parse_string(content)
        assert result.requests[0].name == "myReq"

    def test_env_inside_request_block(self):
        content = "### @env = staging\n### @name = myReq\nGET https://a.com"
        result = parse_string(content)
        assert result.environment_name == "staging"
        assert result.requests[0].name == "myReq"

    def test_header_contains_assertions_and_after_hook(self):
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
        assert req.after_hook.function_name == "cleanup"


class TestUnrecognizedDirectives:
    """`### @word` names with no meaning in the spec are skipped like comments.

    Regression: an unrecognized directive used to break out of the block-header
    loop, which then abandoned the whole block and silently dropped an
    already-parsed @name -- breaking response chaining with no error. See
    docs/spec.md §9.1, which reserves this syntax without assigning it meaning.
    """

    def test_unrecognized_directive_preserves_preceding_name(self):
        content = textwrap.dedent("""\
            ### @name = getThing
            ### @base_url = https://api.example.com
            GET {{base_url}}/things
        """)
        result = parse_string(content)
        assert len(result.requests) == 1
        assert result.requests[0].name == "getThing"

    def test_unrecognized_directive_declares_no_variable(self):
        content = textwrap.dedent("""\
            ### @base_url = https://api.example.com
            GET {{base_url}}/things
        """)
        req = parse_string(content).requests[0]
        assert req.url == "{{base_url}}/things"
        assert req.environment_variables == {}

    def test_name_survives_regardless_of_directive_order(self):
        before = textwrap.dedent("""\
            ### @name = getThing
            ### @unknown = x
            GET https://a.com
        """)
        after = textwrap.dedent("""\
            ### @unknown = x
            ### @name = getThing
            GET https://a.com
        """)
        assert parse_string(before).requests[0].name == "getThing"
        assert parse_string(after).requests[0].name == "getThing"

    def test_unrecognized_directive_does_not_split_the_request(self):
        content = textwrap.dedent("""\
            ### @name = createThing
            ### @retry = 3
            POST https://api.example.com/things
            Content-Type: application/json

            {"name": "x"}
        """)
        result = parse_string(content)
        assert len(result.requests) == 1
        req = result.requests[0]
        assert req.name == "createThing"
        assert req.method == HTTPMethod.POST
        assert req.body.content == '{"name": "x"}'
        assert len(req.headers) == 1

    def test_known_directives_still_delimit_blocks(self):
        content = textwrap.dedent("""\
            ### @name = first
            GET https://a.com

            ### @assert
            # status == 200

            ### @name = second
            GET https://b.com
        """)
        result = parse_string(content)
        assert [r.name for r in result.requests] == ["first", "second"]
        assert len(result.requests[0].assertions.assertions) == 1
