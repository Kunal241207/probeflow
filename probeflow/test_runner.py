"""Execution and reporting for the Phase 1 API test loop."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

from probeflow.client import Response, execute_request
from probeflow.environment import VariableResolutionError, resolve_request
from probeflow.evaluator import AssertionFailure, evaluate_assertion
from probeflow.models import Request


@dataclass
class AssertionResult:
    expression: str
    passed: bool
    message: str | None = None


@dataclass
class RequestTestResult:
    index: int
    name: str
    url: str
    passed: bool
    status_code: int | None = None
    duration_ms: float | None = None
    assertions: list[AssertionResult] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def run_test_suite(
    requests: list[Request],
    variables: dict[str, str],
    timeout: float = 30.0,
) -> list[RequestTestResult]:
    """Run requests in declaration order and evaluate their assertions."""
    results: list[RequestTestResult] = []
    responses: dict[str, Response] = {}

    for index, request in enumerate(requests):
        name = request.name or f"request-{index + 1}"
        result = RequestTestResult(index=index, name=name, url=request.url, passed=False)

        if request.before_hook or request.after_hook:
            result.error = (
                "Script hooks are declared, but hook execution is not implemented. "
                "Refusing to run arbitrary code implicitly."
            )
            results.append(result)
            continue

        try:
            resolved = resolve_request(request, variables, responses)
            result.url = resolved.url
            response = execute_request(resolved, timeout=timeout)
        except (ConnectionError, VariableResolutionError) as exc:
            result.error = str(exc)
            results.append(result)
            continue
        except Exception as exc:
            result.error = f"Request failed: {exc}"
            results.append(result)
            continue

        result.status_code = response.status_code
        result.duration_ms = response.elapsed_ms
        passed = True

        if request.assertions:
            for assertion in request.assertions.assertions:
                try:
                    evaluate_assertion(assertion, response, response.elapsed_ms)
                except AssertionFailure as exc:
                    passed = False
                    result.assertions.append(
                        AssertionResult(
                            expression=assertion.raw_line,
                            passed=False,
                            message=str(exc),
                        )
                    )
                else:
                    result.assertions.append(
                        AssertionResult(expression=assertion.raw_line, passed=True)
                    )

        result.passed = passed
        results.append(result)

        if passed and request.name:
            responses[request.name] = response

    return results


def write_json_report(path: Path, results: list[RequestTestResult]) -> None:
    """Write a stable JSON result document."""
    payload = {
        "passed": all(result.passed for result in results),
        "tests": [result.as_dict() for result in results],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_junit_report(path: Path, results: list[RequestTestResult]) -> None:
    """Write a JUnit XML report consumable by CI systems."""
    suite = ET.Element(
        "testsuite",
        tests=str(len(results)),
        failures=str(sum(not result.passed for result in results)),
    )
    for result in results:
        case = ET.SubElement(
            suite,
            "testcase",
            name=result.name,
            classname="probeflow",
            time=f"{(result.duration_ms or 0) / 1000:.3f}",
        )
        if not result.passed:
            failure = ET.SubElement(case, "failure", message=result.error or "Assertion failed")
            details = [assertion.message for assertion in result.assertions if assertion.message]
            failure.text = "\n".join(details) if details else result.error

    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)
