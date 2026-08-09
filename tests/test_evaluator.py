import pytest

from probeflow.client import Response
from probeflow.evaluator import AssertionFailure, _extract_jsonpath, evaluate_assertion
from probeflow.models import Assertion, AssertionOperator, AssertionTarget


def make_response(status_code=200, json_data=None, headers=None, text=""):
    return Response(
        status_code=status_code,
        status_text="OK",
        elapsed_ms=10.0,
        size_bytes=100,
        headers=headers or {},
        body=text,
        parsed_body=json_data,
        content_type="application/json" if json_data else "text/plain",
        url="https://example.com",
    )


def test_extract_jsonpath():
    data = {
        "user": {"id": 42, "roles": ["admin", "user"], "profile": {"name": "Alice"}},
        "tags": [],
    }

    assert _extract_jsonpath(data, "$.user.id") == 42
    assert _extract_jsonpath(data, "$.user.roles[0]") == "admin"
    assert _extract_jsonpath(data, "$.user.roles[1]") == "user"
    assert _extract_jsonpath(data, "$.user.profile.name") == "Alice"
    assert _extract_jsonpath(data, "$.tags") == []

    # Invalid paths
    assert _extract_jsonpath(data, "user.id") is None
    assert _extract_jsonpath(data, "$.unknown") is None
    assert _extract_jsonpath(data, "$.user.roles[99]") is None
    assert _extract_jsonpath(data, "$.user.roles.invalid") is None
    assert _extract_jsonpath(data, "$.user.id.nested") is None


def test_status_assertion():
    resp = make_response(201)

    # Pass
    evaluate_assertion(
        Assertion(target=AssertionTarget.STATUS, operator=AssertionOperator.EQ, expected=201),
        resp,
        100,
    )

    # Fail
    with pytest.raises(AssertionFailure) as exc:
        evaluate_assertion(
            Assertion(target=AssertionTarget.STATUS, operator=AssertionOperator.EQ, expected=200),
            resp,
            100,
        )
    assert exc.value.expected == 200
    assert exc.value.actual == 201


def test_duration_assertion():
    resp = make_response(200)

    # Pass
    evaluate_assertion(
        Assertion(target=AssertionTarget.DURATION, operator=AssertionOperator.LT, expected=500),
        resp,
        120.5,
    )

    # Fail
    with pytest.raises(AssertionFailure):
        evaluate_assertion(
            Assertion(target=AssertionTarget.DURATION, operator=AssertionOperator.LT, expected=100),
            resp,
            120.5,
        )


def test_header_assertion():
    resp = make_response(headers={"content-type": "application/json", "x-custom": "val"})

    # Pass
    evaluate_assertion(
        Assertion(
            target=AssertionTarget.HEADER,
            path="Content-Type",
            operator=AssertionOperator.EQ,
            expected="application/json",
        ),
        resp,
        10,
    )
    evaluate_assertion(
        Assertion(
            target=AssertionTarget.HEADER,
            path="X-Custom",
            operator=AssertionOperator.EQ,
            expected="val",
        ),
        resp,
        10,
    )

    # Fail
    with pytest.raises(AssertionFailure):
        evaluate_assertion(
            Assertion(
                target=AssertionTarget.HEADER,
                path="Content-Type",
                operator=AssertionOperator.EQ,
                expected="text/html",
            ),
            resp,
            10,
        )


def test_body_jsonpath_assertion():
    resp = make_response(json_data={"name": "Alice", "age": 30})

    # Pass
    evaluate_assertion(
        Assertion(
            target=AssertionTarget.BODY,
            path="$.name",
            operator=AssertionOperator.EQ,
            expected="Alice",
        ),
        resp,
        10,
    )
    evaluate_assertion(
        Assertion(
            target=AssertionTarget.BODY, path="$.age", operator=AssertionOperator.GT, expected=25
        ),
        resp,
        10,
    )

    # Fail
    with pytest.raises(AssertionFailure):
        evaluate_assertion(
            Assertion(
                target=AssertionTarget.BODY,
                path="$.name",
                operator=AssertionOperator.EQ,
                expected="Bob",
            ),
            resp,
            10,
        )


def test_operators():
    resp = make_response(json_data={"val": 10, "text": "hello", "items": [1, 2, 3], "flag": True})

    # IN
    evaluate_assertion(
        Assertion(
            target=AssertionTarget.BODY,
            path="$.val",
            operator=AssertionOperator.IN,
            expected=[5, 10, 15],
        ),
        resp,
        10,
    )
    with pytest.raises(AssertionFailure):
        evaluate_assertion(
            Assertion(
                target=AssertionTarget.BODY,
                path="$.val",
                operator=AssertionOperator.IN,
                expected=[1, 2],
            ),
            resp,
            10,
        )

    # CONTAINS
    evaluate_assertion(
        Assertion(
            target=AssertionTarget.BODY,
            path="$.text",
            operator=AssertionOperator.CONTAINS,
            expected="ell",
        ),
        resp,
        10,
    )
    evaluate_assertion(
        Assertion(
            target=AssertionTarget.BODY,
            path="$.items",
            operator=AssertionOperator.CONTAINS,
            expected=2,
        ),
        resp,
        10,
    )

    # MATCHES
    evaluate_assertion(
        Assertion(
            target=AssertionTarget.BODY,
            path="$.text",
            operator=AssertionOperator.MATCHES,
            expected="^he.*o$",
        ),
        resp,
        10,
    )
    with pytest.raises(AssertionFailure):
        evaluate_assertion(
            Assertion(
                target=AssertionTarget.BODY,
                path="$.text",
                operator=AssertionOperator.MATCHES,
                expected="^[0-9]+$",
            ),
            resp,
            10,
        )

    # EXISTS / NOT_EXISTS
    evaluate_assertion(
        Assertion(
            target=AssertionTarget.BODY,
            path="$.val",
            operator=AssertionOperator.EXISTS,
            expected=None,
            raw_line="$.val exists",
        ),
        resp,
        10,
    )
    evaluate_assertion(
        Assertion(
            target=AssertionTarget.BODY,
            path="$.missing",
            operator=AssertionOperator.NOT_EXISTS,
            expected=None,
            raw_line="$.missing not exists",
        ),
        resp,
        10,
    )

    # IS type
    evaluate_assertion(
        Assertion(
            target=AssertionTarget.BODY,
            path="$.text",
            operator=AssertionOperator.IS,
            expected="string",
        ),
        resp,
        10,
    )
    evaluate_assertion(
        Assertion(
            target=AssertionTarget.BODY,
            path="$.val",
            operator=AssertionOperator.IS,
            expected="number",
        ),
        resp,
        10,
    )
    evaluate_assertion(
        Assertion(
            target=AssertionTarget.BODY,
            path="$.flag",
            operator=AssertionOperator.IS,
            expected="boolean",
        ),
        resp,
        10,
    )
    evaluate_assertion(
        Assertion(
            target=AssertionTarget.BODY,
            path="$.items",
            operator=AssertionOperator.IS,
            expected="array",
        ),
        resp,
        10,
    )


class TestIsTypeOperator:
    """Targeted tests for the IS type-check operator, including edge cases."""

    def _make(self, json_data):
        return make_response(json_data=json_data)

    def _assert_is(self, value_key, type_name, json_data, should_pass: bool):
        resp = self._make(json_data)
        a = Assertion(
            target=AssertionTarget.BODY,
            path=f"$.{value_key}",
            operator=AssertionOperator.IS,
            expected=type_name,
        )
        if should_pass:
            evaluate_assertion(a, resp, 10)
        else:
            with pytest.raises(AssertionFailure):
                evaluate_assertion(a, resp, 10)

    # --- string ---
    def test_is_string_passes(self):
        self._assert_is("v", "string", {"v": "hello"}, should_pass=True)

    def test_is_string_fails_for_int(self):
        self._assert_is("v", "string", {"v": 42}, should_pass=False)

    # --- number ---
    def test_is_number_passes_for_int(self):
        self._assert_is("v", "number", {"v": 42}, should_pass=True)

    def test_is_number_passes_for_float(self):
        self._assert_is("v", "number", {"v": 3.14}, should_pass=True)

    def test_is_number_fails_for_string(self):
        self._assert_is("v", "number", {"v": "42"}, should_pass=False)

    def test_is_number_fails_for_boolean(self):
        """bool is a subclass of int in Python — must NOT pass as 'number'."""
        self._assert_is("v", "number", {"v": True}, should_pass=False)
        self._assert_is("v", "number", {"v": False}, should_pass=False)

    # --- boolean ---
    def test_is_boolean_passes(self):
        self._assert_is("v", "boolean", {"v": True}, should_pass=True)
        self._assert_is("v", "boolean", {"v": False}, should_pass=True)

    def test_is_boolean_fails_for_int(self):
        self._assert_is("v", "boolean", {"v": 1}, should_pass=False)

    # --- object ---
    def test_is_object_passes(self):
        self._assert_is("v", "object", {"v": {"key": "val"}}, should_pass=True)

    def test_is_object_fails_for_list(self):
        self._assert_is("v", "object", {"v": [1, 2]}, should_pass=False)

    # --- array ---
    def test_is_array_passes(self):
        self._assert_is("v", "array", {"v": [1, 2, 3]}, should_pass=True)

    def test_is_array_fails_for_object(self):
        self._assert_is("v", "array", {"v": {"a": 1}}, should_pass=False)

    # --- unknown type name ---
    def test_unknown_type_name_raises(self):
        resp = self._make({"v": "hello"})
        with pytest.raises(AssertionFailure, match="Unknown type name"):
            evaluate_assertion(
                Assertion(
                    target=AssertionTarget.BODY,
                    path="$.v",
                    operator=AssertionOperator.IS,
                    expected="integer",
                ),
                resp,
                10,
            )

    # --- NE operator (not covered elsewhere) ---
    def test_ne_passes_when_different(self):
        resp = make_response(201)
        evaluate_assertion(
            Assertion(target=AssertionTarget.STATUS, operator=AssertionOperator.NE, expected=200),
            resp,
            10,
        )

    def test_ne_fails_when_equal(self):
        resp = make_response(200)
        with pytest.raises(AssertionFailure):
            evaluate_assertion(
                Assertion(
                    target=AssertionTarget.STATUS, operator=AssertionOperator.NE, expected=200
                ),
                resp,
                10,
            )

    # --- Numeric comparison edge cases ---
    def test_numeric_compare_fails_on_null(self):
        resp = make_response(json_data={"v": None})
        with pytest.raises(AssertionFailure, match="null"):
            evaluate_assertion(
                Assertion(
                    target=AssertionTarget.BODY,
                    path="$.v",
                    operator=AssertionOperator.LT,
                    expected=100,
                ),
                resp,
                10,
            )

    def test_numeric_compare_fails_on_non_numeric_string(self):
        resp = make_response(json_data={"v": "abc"})
        with pytest.raises(AssertionFailure, match="numerically compare"):
            evaluate_assertion(
                Assertion(
                    target=AssertionTarget.BODY,
                    path="$.v",
                    operator=AssertionOperator.GT,
                    expected=0,
                ),
                resp,
                10,
            )

    # --- CONTAINS edge cases ---
    def test_contains_fails_on_null_actual(self):
        resp = make_response(json_data={"v": None})
        with pytest.raises(AssertionFailure, match="null"):
            evaluate_assertion(
                Assertion(
                    target=AssertionTarget.BODY,
                    path="$.v",
                    operator=AssertionOperator.CONTAINS,
                    expected="x",
                ),
                resp,
                10,
            )

    def test_contains_fails_on_type_mismatch(self):
        resp = make_response(json_data={"v": 42})
        with pytest.raises(AssertionFailure):
            evaluate_assertion(
                Assertion(
                    target=AssertionTarget.BODY,
                    path="$.v",
                    operator=AssertionOperator.CONTAINS,
                    expected="4",
                ),
                resp,
                10,
            )

    # --- MATCHES edge cases ---
    def test_matches_fails_on_invalid_regex(self):
        resp = make_response(json_data={"v": "hello"})
        with pytest.raises(AssertionFailure, match="Invalid regex"):
            evaluate_assertion(
                Assertion(
                    target=AssertionTarget.BODY,
                    path="$.v",
                    operator=AssertionOperator.MATCHES,
                    expected="[unclosed",
                ),
                resp,
                10,
            )

    def test_matches_fails_on_non_string_actual(self):
        resp = make_response(json_data={"v": 42})
        with pytest.raises(AssertionFailure, match="requires strings"):
            evaluate_assertion(
                Assertion(
                    target=AssertionTarget.BODY,
                    path="$.v",
                    operator=AssertionOperator.MATCHES,
                    expected="\\d+",
                ),
                resp,
                10,
            )

    # --- EXISTS on missing header ---
    def test_exists_fails_for_missing_header(self):
        resp = make_response(headers={})
        with pytest.raises(AssertionFailure):
            evaluate_assertion(
                Assertion(
                    target=AssertionTarget.HEADER,
                    path="X-Missing",
                    operator=AssertionOperator.EXISTS,
                    raw_line="header.X-Missing exists",
                ),
                resp,
                10,
            )

    # --- NOT_EXISTS fails when present ---
    def test_not_exists_fails_when_present(self):
        resp = make_response(headers={"x-present": "yes"})
        with pytest.raises(AssertionFailure):
            evaluate_assertion(
                Assertion(
                    target=AssertionTarget.HEADER,
                    path="X-Present",
                    operator=AssertionOperator.NOT_EXISTS,
                    raw_line="header.X-Present not exists",
                ),
                resp,
                10,
            )

    # --- IN with non-list expected ---
    def test_in_fails_with_non_list_expected(self):
        resp = make_response(200)
        with pytest.raises(AssertionFailure, match="requires a list"):
            evaluate_assertion(
                Assertion(
                    target=AssertionTarget.STATUS,
                    operator=AssertionOperator.IN,
                    expected="200",
                ),
                resp,
                10,
            )

    # --- body path on non-JSON body ---
    def test_body_jsonpath_on_non_json_returns_none(self):
        resp = make_response(text="plain text")
        with pytest.raises(AssertionFailure):
            evaluate_assertion(
                Assertion(
                    target=AssertionTarget.BODY,
                    path="$.key",
                    operator=AssertionOperator.EXISTS,
                    raw_line="body.$.key exists",
                ),
                resp,
                10,
            )
