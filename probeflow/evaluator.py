"""Assertion evaluation against HTTP responses."""

from __future__ import annotations

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
        header_name = assertion.path or ""
        return next(
            (v for k, v in response.headers.items() if k.lower() == header_name.lower()),
            None,
        )

    if assertion.target == AssertionTarget.BODY:
        if not assertion.path or assertion.path == "$":
            if response.parsed_body is not None:
                return response.parsed_body
            return response.body

        if response.parsed_body is None:
            return None

        return _extract_jsonpath(response.parsed_body, assertion.path)

    return None


def _extract_jsonpath(data: Any, path: str) -> Any:
    if not path.startswith("$."):
        return None

    current = data
    parts = path[2:].replace("][", "].[").replace("[", ".[").split(".")

    for part in parts:
        if not part:
            continue

        if part.startswith("[") and part.endswith("]"):
            try:
                idx = int(part[1:-1])
                if isinstance(current, list) and 0 <= idx < len(current):
                    current = current[idx]
                else:
                    return None
            except ValueError:
                return None
        else:
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
        type_checks = {
            "string": lambda x: isinstance(x, str),
            "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
            "boolean": lambda x: isinstance(x, bool),
            "object": lambda x: isinstance(x, dict),
            "array": lambda x: isinstance(x, list),
            "null": lambda x: x is None,
        }
        checker = type_checks.get(str(expected))
        if checker is None:
            raise AssertionFailure(
                f"Unknown type name: {expected}",
                expected,
                actual,
            )
        if not checker(actual):
            raise AssertionFailure(
                f"Expected type {expected}, got {type(actual).__name__}", expected, actual
            )
