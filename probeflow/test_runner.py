"""Execution and reporting for probeflow test commands."""

from __future__ import annotations

import difflib
import json
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

from probeflow.client import OAuth2TokenProvider, Response, execute_request
from probeflow.environment import (
    EnvironmentNotFoundError,
    VariableResolutionError,
    find_unresolved_variables,
    load_environment,
    resolve_request,
)
from probeflow.evaluator import AssertionFailure, evaluate_assertion
from probeflow.models import ParseError, Request
from probeflow.parser import parse_file


@dataclass
class AssertionResult:
    expression: str
    passed: bool
    message: str | None = None
    expected: object | None = None
    actual: object | None = None


@dataclass
class RequestTestResult:
    index: int
    name: str
    url: str
    passed: bool
    source: str | None = None
    status_code: int | None = None
    duration_ms: float | None = None
    assertions: list[AssertionResult] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _render_assertion_value(value: object) -> str:
    try:
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def format_assertion_failure(
    expression: str, message: str, expected: object, actual: object
) -> str:
    """Return a compact unified diff for a failed assertion."""
    expected_lines = _render_assertion_value(expected).splitlines() or [""]
    actual_lines = _render_assertion_value(actual).splitlines() or [""]
    diff = "\n".join(
        difflib.unified_diff(
            expected_lines,
            actual_lines,
            fromfile="expected",
            tofile="actual",
            lineterm="",
        )
    )
    return f"Assertion failed: {expression}\n{message}\nExpected vs actual:\n{diff}"


def run_test_suite(
    requests: list[Request],
    variables: dict[str, str],
    timeout: float = 30.0,
    source: str | None = None,
    base_dir: Path | None = None,
) -> list[RequestTestResult]:
    """Run requests in declaration order and evaluate their assertions."""
    # Check for duplicate request names
    seen_names: set[str] = set()
    for request in requests:
        if request.name:
            if request.name in seen_names:
                results: list[RequestTestResult] = []
                results.append(
                    RequestTestResult(
                        index=0,
                        name="duplicate-name-check",
                        url=source or "unknown",
                        passed=False,
                        source=source,
                        error=(
                            f"Duplicate request name: '{request.name}'. "
                            "Request names must be unique within a file "
                            "for response chaining."
                        ),
                    )
                )
                return results
            seen_names.add(request.name)

    results = []
    responses: dict[str, Response] = {}
    token_provider = OAuth2TokenProvider()

    for index, request in enumerate(requests):
        name = request.name or f"request-{index + 1}"
        result = RequestTestResult(
            index=index,
            name=name,
            url=request.url,
            passed=False,
            source=source,
        )

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

            # Check for unresolved variables in the resolved request
            unresolved: list[str] = []
            unresolved.extend(find_unresolved_variables(resolved.url, variables, responses))
            for header in resolved.headers:
                unresolved.extend(find_unresolved_variables(header.name, variables, responses))
                unresolved.extend(find_unresolved_variables(header.value, variables, responses))
            if resolved.body:
                unresolved.extend(
                    find_unresolved_variables(resolved.body.content, variables, responses)
                )
            if resolved.oauth2:
                unresolved.extend(
                    find_unresolved_variables(resolved.oauth2.token_url, variables, responses)
                )
                unresolved.extend(
                    find_unresolved_variables(resolved.oauth2.client_id, variables, responses)
                )
                unresolved.extend(
                    find_unresolved_variables(resolved.oauth2.client_secret, variables, responses)
                )
                for scope in resolved.oauth2.scopes:
                    unresolved.extend(find_unresolved_variables(scope, variables, responses))
            for part in resolved.multipart:
                unresolved.extend(find_unresolved_variables(part.name, variables, responses))
                if part.value:
                    unresolved.extend(find_unresolved_variables(part.value, variables, responses))
                if part.file_path:
                    unresolved.extend(
                        find_unresolved_variables(part.file_path, variables, responses)
                    )
                if part.content_type:
                    unresolved.extend(
                        find_unresolved_variables(part.content_type, variables, responses)
                    )

            if unresolved:
                unique_unresolved = sorted(set(unresolved))
                vars_str = ", ".join("{{" + v + "}}" for v in unique_unresolved)
                result.error = f"Unresolved variables: {vars_str}"
                results.append(result)
                continue

            response = execute_request(
                resolved,
                timeout=timeout,
                token_provider=token_provider,
                base_dir=base_dir,
            )
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
                            message=format_assertion_failure(
                                assertion.raw_line,
                                str(exc),
                                exc.expected,
                                exc.actual,
                            ),
                            expected=exc.expected,
                            actual=exc.actual,
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


def discover_http_files(target: Path) -> list[Path]:
    """Discover one file or a deterministic recursive directory collection."""
    if target.is_file():
        if target.suffix.lower() != ".http":
            raise ValueError(f"Expected a .http file, got: {target}")
        return [target]
    if not target.is_dir():
        raise ValueError(f"Test target does not exist: {target}")

    return sorted(
        (
            path
            for path in target.rglob("*.http")
            if not any(part.startswith(".") or part == "__pycache__" for part in path.parts)
        ),
        key=lambda path: path.as_posix(),
    )


def run_test_target(
    target: Path,
    env_name: str | None = None,
    timeout: float = 30.0,
) -> tuple[list[RequestTestResult], int]:
    """Run a file or directory collection, keeping environments and chains file-scoped."""
    files = discover_http_files(target)
    if not files:
        return (
            [
                RequestTestResult(
                    index=0,
                    name="collection",
                    url=str(target),
                    passed=False,
                    error=f"No .http files found under {target}",
                )
            ],
            0,
        )

    root = target if target.is_dir() else target.parent
    results: list[RequestTestResult] = []
    for path in files:
        source = str(path.relative_to(root)) if target.is_dir() else path.name
        try:
            request_file = parse_file(path)
        except (ParseError, OSError) as exc:
            results.append(
                RequestTestResult(
                    index=0,
                    name="parse",
                    url=str(path),
                    passed=False,
                    source=source,
                    error=str(exc),
                )
            )
            continue

        if not request_file.requests:
            results.append(
                RequestTestResult(
                    index=0,
                    name="empty",
                    url=str(path),
                    passed=False,
                    source=source,
                    error="No requests found in file",
                )
            )
            continue

        try:
            environment = load_environment(path.parent, env_name or request_file.environment_name)
        except EnvironmentNotFoundError as exc:
            results.append(
                RequestTestResult(
                    index=0,
                    name="environment",
                    url=str(path),
                    passed=False,
                    source=source,
                    error=str(exc),
                )
            )
            continue

        variables = environment.variables if environment else {}
        results.extend(
            run_test_suite(
                request_file.requests,
                variables,
                timeout=timeout,
                source=source,
                base_dir=path.parent,
            )
        )

    return results, len(files)


def write_json_report(path: Path, results: list[RequestTestResult]) -> None:
    payload = {
        "passed": all(result.passed for result in results),
        "tests": [result.as_dict() for result in results],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_junit_report(path: Path, results: list[RequestTestResult]) -> None:
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
            classname=f"probeflow.{result.source}" if result.source else "probeflow",
            time=f"{(result.duration_ms or 0) / 1000:.3f}",
        )
        if not result.passed:
            failure = ET.SubElement(case, "failure", message=result.error or "Assertion failed")
            details = [a.message for a in result.assertions if not a.passed and a.message]
            failure.text = "\n".join(details) if details else (result.error or "")

    tree = ET.ElementTree(suite)
    ET.indent(tree, space="  ")
    with path.open("wb") as fh:
        tree.write(fh, encoding="utf-8", xml_declaration=True)
    # Append a trailing newline so the file is text-editor friendly
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n")
