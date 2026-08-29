"""Round-trip tests for the .http parser/formatter.

Invariant: parse → format → parse reproduces the same AST (ignoring source
spans, which change on reformat). Two layers, neither touching the golden-file
tests: a deterministic parametrized set that always runs, and a hypothesis
fuzzer that runs only when hypothesis is installed (the ``dev`` extra).
"""

from __future__ import annotations

import pytest

from probeflow.parser import format_file, parse_string

# ── Shared helpers ──────────────────────────────────────────────────────────

IDENT_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"

METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

HEADER_NAMES = ["Authorization", "Accept", "X-Foo", "X-Request-Id", "User-Agent"]

# Canonical, valid assertion lines covering every operator/target the grammar
# supports. Each is already in the exact spelling the formatter emits, so it
# round-trips through `raw_line`.
CANONICAL_ASSERTIONS = [
    "status == 200",
    "status != 404",
    "status < 500",
    "status >= 200",
    "status in [200, 201, 204]",
    'body.$.name == "Alice"',
    "body.$.count == 42",
    "body.$.price == 9.99",
    "body.$.active == true",
    "body.$.disabled == false",
    "body.$.token is null",
    "body.$.id exists",
    "body.$.missing not exists",
    'body.$.tags contains "x"',
    'body.$.email matches "^.+@.+$"',
    'body.$ == {"a": 1, "b": [2, 3]}',
    'body.$.items == [{"id": 1}, {"id": 2}]',
    'header.Content-Type == "application/json"',
    "header.X-Request-Id exists",
    "duration < 1000ms",
    "duration <= 250ms",
]


def _strip_spans(obj):
    """Recursively drop every ``span`` key so the comparison ignores source
    positions, which change when a document is reformatted."""
    if isinstance(obj, dict):
        return {k: _strip_spans(v) for k, v in obj.items() if k != "span"}
    if isinstance(obj, list):
        return [_strip_spans(v) for v in obj]
    return obj


def _normalize(request_file):
    """Structural view of a RequestFile for equality, ignoring spans and the
    filename (which differs between the original and formatted sources)."""
    dumped = request_file.model_dump(mode="json")
    dumped.pop("filename", None)
    return _strip_spans(dumped)


def _assert_roundtrips(text: str) -> None:
    """parse → format → parse must reproduce the same AST (modulo spans)."""
    ast1 = parse_string(text, filename="<input>")
    assert ast1.requests, "test fixture must contain at least one request"
    formatted = format_file(ast1)
    ast2 = parse_string(formatted, filename="<formatted>")
    assert _normalize(ast1) == _normalize(ast2), (
        f"round-trip changed the AST\n--- ORIGINAL ---\n{text}\n--- FORMATTED ---\n{formatted}"
    )


# ── Layer 1: deterministic, dependency-free round-trip cases ────────────────

DETERMINISTIC_CASES = {
    "minimal_get": "GET https://api.example.com/users\n",
    "empty_header_value": (
        "GET https://api.example.com/users\nX-Empty:\nAccept: application/json\n"
    ),
    "header_internal_spaces": (
        "GET https://api.example.com/users\nX-Note: value  with   internal    spaces\n"
    ),
    "multiline_body_with_comment_and_blank": (
        "POST https://api.example.com/items\n\n"
        "line one\n# not a directive, this is body content\n\nline two\n"
    ),
    "json_body_with_content_type": (
        "POST https://api.example.com/items\nContent-Type: application/json\n\n"
        '{"name": "Alice", "age": 30}\n'
    ),
    "root_object_literal_assertion": (
        'GET https://api.example.com/x\n\n### @assert\n# body.$ == {"a": 1, "b": [2, 3]}\n'
    ),
    "status_in_list_assertion": (
        "GET https://api.example.com/x\n\n### @assert\n# status in [200, 201, 204]\n"
    ),
    "oauth2_with_scopes": (
        "### @oauth2 = client-credentials https://auth.example.com/token cid secret "
        "read write\nGET https://api.example.com/secure\n"
    ),
    "multipart_form_and_file": (
        "### @form = field1=\n"
        "### @form = field2=value2\n"
        "### @file = upload=files/data.bin;type=application/octet-stream\n"
        "POST https://api.example.com/upload\n"
    ),
    "before_after_hooks_around_assert": (
        "### @name = signed\n### @before = hooks/auth.py:sign_request\n"
        "GET https://api.example.com/secure\n\n### @assert\n# status == 200\n"
        "### @after = hooks/log.py:record\n"
    ),
    "file_env_and_two_requests": (
        "### @env = staging\n\n"
        "### @name = login\nPOST https://api.example.com/login\n\n"
        '{"user": "a"}\n\n###\n\n'
        "### @name = profile\nGET https://api.example.com/me\n"
        "Authorization: Bearer {{login.response.body.$.token}}\n"
    ),
    "http_version_and_variable_url": ("GET https://api.example.com/{{resource}} HTTP/1.1\n"),
    "all_assertion_operators": (
        "GET https://api.example.com/x\n\n### @assert\n"
        "# status == 200\n"
        "# body.$.id exists\n"
        "# body.$.deleted not exists\n"
        "# body.$.token is null\n"
        '# body.$.tags contains "x"\n'
        '# body.$.email matches "^.+$"\n'
        '# header.Content-Type == "application/json"\n'
        "# duration < 1000ms\n"
    ),
}


@pytest.mark.parametrize("text", DETERMINISTIC_CASES.values(), ids=list(DETERMINISTIC_CASES))
def test_deterministic_roundtrip(text):
    _assert_roundtrips(text)


# ── Layer 2: hypothesis property test (only when the dep is available) ───────

try:
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st

    HAS_HYPOTHESIS = True
except ImportError:  # pragma: no cover - exercised only when dev deps absent
    HAS_HYPOTHESIS = False


def test_property_layer_requires_hypothesis():
    """Make the fuzzing layer's status explicit in a test run: a visible skip
    when hypothesis is missing, a trivial pass when it is present (the real work
    is done by ``test_parse_format_parse_roundtrip``)."""
    if not HAS_HYPOTHESIS:
        pytest.skip("hypothesis not installed; run `pip install -e '.[dev]'` to enable")


if HAS_HYPOTHESIS:
    _idents = st.text(alphabet=IDENT_ALPHABET, min_size=1, max_size=8)

    def _header_values():
        return st.one_of(
            st.just(""),
            st.just("application/json"),
            _idents,
            st.just("value  with   internal  spaces"),
            st.builds(lambda s: "{{" + s + "}}", _idents),
            st.builds(lambda s: "Bearer " + s, _idents),
        )

    @st.composite
    def _url(draw):
        host = draw(_idents).lower()
        depth = draw(st.integers(min_value=0, max_value=3))
        path = "/".join(draw(_idents) for _ in range(depth))
        if draw(st.booleans()):
            return f"https://{host}.example.com/{{{{{draw(_idents)}}}}}"
        return f"https://{host}.example.com/{path}"

    @st.composite
    def _body(draw):
        kind = draw(st.sampled_from(["json_obj", "json_arr", "text", "multiline"]))
        if kind == "json_obj":
            return '{"key": "value", "n": ' + str(draw(st.integers(0, 999))) + "}"
        if kind == "json_arr":
            return "[1, 2, 3]"
        if kind == "text":
            return "plain body " + draw(_idents)
        # multiline: a leading-# comment and an internal blank line are absorbed
        # into the body as long as no line begins with ### (which we never emit).
        return "line one\n# not a directive\n\nline two " + draw(_idents)

    @st.composite
    def _request_block(draw):
        lines: list[str] = []
        use_multipart = draw(st.booleans())

        if draw(st.booleans()):
            lines.append(f"### @name = {draw(_idents)}")
        if draw(st.booleans()):
            lines.append(f"### @before = hooks/{draw(_idents)}.py:{draw(_idents)}")
        if draw(st.booleans()):
            n_scopes = draw(st.integers(min_value=0, max_value=3))
            scopes = "".join(" " + draw(_idents) for _ in range(n_scopes))
            lines.append(
                "### @oauth2 = client-credentials "
                f"https://auth.example.com/token {draw(_idents)} {draw(_idents)}{scopes}"
            )
        if use_multipart:
            for _ in range(draw(st.integers(min_value=1, max_value=3))):
                if draw(st.booleans()):
                    value = "" if draw(st.booleans()) else draw(_idents)
                    lines.append(f"### @form = {draw(_idents)}={value}")
                else:
                    suffix = ";type=text/plain" if draw(st.booleans()) else ""
                    lines.append(f"### @file = {draw(_idents)}=files/{draw(_idents)}.bin{suffix}")

        method = draw(st.sampled_from(METHODS))
        if draw(st.booleans()):
            method = method.lower()  # tokenizer is case-insensitive on methods
        request_line = f"{method} {draw(_url())}"
        if draw(st.booleans()):
            request_line += " HTTP/1.1"
        lines.append(request_line)

        added_content_type = False
        for _ in range(draw(st.integers(min_value=0, max_value=3))):
            if not use_multipart and not added_content_type and draw(st.booleans()):
                lines.append("Content-Type: application/json")
                added_content_type = True
            else:
                lines.append(f"{draw(st.sampled_from(HEADER_NAMES))}: {draw(_header_values())}")

        if draw(st.booleans()):
            lines.append("")
            lines.append(draw(_body()))

        if draw(st.booleans()):
            lines.append("")
            lines.append("### @assert")
            for _ in range(draw(st.integers(min_value=1, max_value=4))):
                lines.append(f"# {draw(st.sampled_from(CANONICAL_ASSERTIONS))}")

        if draw(st.booleans()):
            lines.append(f"### @after = hooks/{draw(_idents)}.py:{draw(_idents)}")

        return "\n".join(lines)

    @st.composite
    def _http_file(draw):
        blocks = draw(st.lists(_request_block(), min_size=1, max_size=4))
        text = "\n\n###\n\n".join(blocks)
        if draw(st.booleans()):
            text = f"### @env = {draw(_idents)}\n\n" + text
        return text + "\n"

    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    @given(text=_http_file())
    def test_parse_format_parse_roundtrip(text):
        _assert_roundtrips(text)
