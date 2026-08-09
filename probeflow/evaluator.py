import re
from typing import Any

from probeflow.client import Response
from probeflow.models import Assertion, AssertionOperator, AssertionTarget


class AssertionFailure(Exception):
    """Raised when an assertion fails."""

    def __init__(self, message: str, expected: Any, actual: Any):
        super().__init__(message)
        self.message = message
        self.expected = expected
        self.actual = actual


def evaluate_assertion(assertion: Assertion, response: Response, duration_ms: float) -> None:
    """Evaluate an assertion against an HTTP response.

    Raises:
        AssertionFailure: If the assertion fails.
    """
    actual_value = _extract_actual_value(assertion, response, duration_ms)

    if assertion.operator == AssertionOperator.EXISTS:
        if actual_value is None:
            raise AssertionFailure(
                f"Expected {assertion.raw_line.split()[0]} to exist",
                expected="<exists>",
                actual=None,
            )
        return

    if assertion.operator == AssertionOperator.NOT_EXISTS:
        if actual_value is not None:
            raise AssertionFailure(
                f"Expected {assertion.raw_line.split()[0]} to not exist",
                expected="<not exists>",
                actual=actual_value,
            )
        return

    _compare_values(assertion, actual_value)


def _extract_actual_value(assertion: Assertion, response: Response, duration_ms: float) -> Any:
    if assertion.target == AssertionTarget.STATUS:
        return response.status_code

    if assertion.target == AssertionTarget.DURATION:
        return duration_ms

    if assertion.target == AssertionTarget.HEADER:
        # Case-insensitive header lookup
        header_name = assertion.path or ""
        return next(
            (
                value
                for name, value in response.headers.items()
                if name.lower() == header_name.lower()
            ),
            None,
        )

    if assertion.target == AssertionTarget.BODY:
        if not assertion.path or assertion.path == "$":
            return response.body

        if response.parsed_body is None:
            return None  # Cannot extract JSONPath from non-JSON body

        return _extract_jsonpath(response.parsed_body, assertion.path)

    return None


def _extract_jsonpath(data: Any, path: str) -> Any:
    """Extract a value from a JSON object using a subset of JSONPath.

    Supports: $.field, $.array[0], $.field.nested
    """
    if not path.startswith("$."):
        return None

    current = data
    parts = path[2:].replace("][", "].[").replace("[", ".[").split(".")

    for part in parts:
        if not part:
            continue

        if part.startswith("[") and part.endswith("]"):
            # Array index
            try:
                idx = int(part[1:-1])
                if isinstance(current, list) and 0 <= idx < len(current):
                    current = current[idx]
                else:
                    return None
            except ValueError:
                return None
        else:
            # Object key
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None

    return current


def _compare_values(assertion: Assertion, actual: Any) -> None:
    expected = assertion.expected
    op = assertion.operator

    if op == AssertionOperator.EQ:
        if actual != expected:
            raise AssertionFailure(f"Expected {expected}, got {actual}", expected, actual)

    elif op == AssertionOperator.NE:
        if actual == expected:
            raise AssertionFailure(f"Expected not {expected}, but got {actual}", expected, actual)

    elif op in (
        AssertionOperator.LT,
        AssertionOperator.LE,
        AssertionOperator.GT,
        AssertionOperator.GE,
    ):
        # Type coercion for comparison
        if actual is None:
            raise AssertionFailure(f"Cannot compare {op.value} on null", expected, actual)

        try:
            act_float = float(actual)
            exp_float = float(expected)
        except (ValueError, TypeError):
            raise AssertionFailure(
                f"Cannot numerically compare '{actual}' and '{expected}'", expected, actual
            )

        if op == AssertionOperator.LT and not (act_float < exp_float):
            raise AssertionFailure(f"Expected < {expected}, got {actual}", expected, actual)
        elif op == AssertionOperator.LE and not (act_float <= exp_float):
            raise AssertionFailure(f"Expected <= {expected}, got {actual}", expected, actual)
        elif op == AssertionOperator.GT and not (act_float > exp_float):
            raise AssertionFailure(f"Expected > {expected}, got {actual}", expected, actual)
        elif op == AssertionOperator.GE and not (act_float >= exp_float):
            raise AssertionFailure(f"Expected >= {expected}, got {actual}", expected, actual)

    elif op == AssertionOperator.IN:
        if not isinstance(expected, list):
            raise AssertionFailure(
                f"'in' operator requires a list, got {type(expected).__name__}", expected, actual
            )
        if actual not in expected:
            raise AssertionFailure(f"Expected {actual} to be in {expected}", expected, actual)

    elif op == AssertionOperator.CONTAINS:
        if actual is None or expected is None:
            raise AssertionFailure("Cannot use 'contains' with null", expected, actual)
        try:
            if expected not in actual:
                raise AssertionFailure(
                    f"Expected '{actual}' to contain '{expected}'", expected, actual
                )
        except TypeError:
            raise AssertionFailure(
                f"Cannot check if {type(actual).__name__} contains {type(expected).__name__}",
                expected,
                actual,
            )

    elif op == AssertionOperator.MATCHES:
        if not isinstance(actual, str) or not isinstance(expected, str):
            raise AssertionFailure("'matches' operator requires strings", expected, actual)
        try:
            if not re.search(expected, actual):
                raise AssertionFailure(
                    f"Expected '{actual}' to match regex '{expected}'", expected, actual
                )
        except re.error as e:
            raise AssertionFailure(f"Invalid regex '{expected}': {e}", expected, actual)

    elif op == AssertionOperator.IS:
        _valid_type_names = {"string", "number", "boolean", "object", "array"}
        if expected not in _valid_type_names:
            raise AssertionFailure(
                f"Unknown type name '{expected}'. "
                f"Valid types: {', '.join(sorted(_valid_type_names))}",
                expected,
                actual,
            )
        if expected == "string" and not isinstance(actual, str):
            raise AssertionFailure(
                f"Expected type string, got {type(actual).__name__}", expected, actual
            )
        elif expected == "number" and (
            not isinstance(actual, (int, float)) or isinstance(actual, bool)
        ):
            # bool is a subclass of int in Python — exclude it from the number check
            raise AssertionFailure(
                f"Expected type number, got {type(actual).__name__}", expected, actual
            )
        elif expected == "boolean" and not isinstance(actual, bool):
            raise AssertionFailure(
                f"Expected type boolean, got {type(actual).__name__}", expected, actual
            )
        elif expected == "object" and not isinstance(actual, dict):
            raise AssertionFailure(
                f"Expected type object, got {type(actual).__name__}", expected, actual
            )
        elif expected == "array" and not isinstance(actual, list):
            raise AssertionFailure(
                f"Expected type array, got {type(actual).__name__}", expected, actual
            )
