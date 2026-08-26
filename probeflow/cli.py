"""CLI interface for probeflow using Typer."""

from __future__ import annotations

import difflib
import re
from pathlib import Path

import typer

from probeflow import __spec_version__, __version__
from probeflow.client import OAuth2TokenProvider, Response, execute_request
from probeflow.environment import load_environment, resolve_request
from probeflow.evaluator import AssertionFailure, evaluate_assertion
from probeflow.formatter import (
    configure_output,
    make_console,
    print_error,
    print_request_summary,
    print_response,
)
from probeflow.models import ParseError
from probeflow.parser import format_file, parse_file
from probeflow.test_runner import run_test_target, write_json_report, write_junit_report

app = typer.Typer(
    name="probeflow",
    help="Pytest for HTTP APIs using .http request files.",
    no_args_is_help=True,
    add_completion=True,
)


def app_entry() -> None:
    app()


@app.callback()
def main(
    no_color: bool = typer.Option(
        False,
        "--no-color",
        help="Disable ANSI color output.",
    ),
) -> None:
    """Configure shared CLI output before executing a command."""
    configure_output(no_color=no_color)


@app.command()
def run(
    file: Path = typer.Argument(
        ...,
        help="Path to the .http file to execute.",
        exists=True,
        readable=True,
        dir_okay=False,
    ),
    request_index: int = typer.Option(
        None,
        "--index",
        "-i",
        help="Index of the request to run (0-based). Runs all if omitted.",
    ),
    env_name: str | None = typer.Option(
        None,
        "--env",
        "-e",
        help="Environment name (e.g., dev, staging, prod). Uses .env.{name}.",
    ),
    show_headers: bool = typer.Option(
        False,
        "--headers",
        "-H",
        help="Show response headers.",
    ),
    timeout: float = typer.Option(
        30.0,
        "--timeout",
        "-t",
        help="Request timeout in seconds.",
        min=1.0,
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress output, only show errors.",
    ),
    check_assertions: bool = typer.Option(
        False,
        "--check-assertions",
        help="Evaluate assertions after each request.",
    ),
) -> None:
    """Execute one or more HTTP requests from a .http file."""
    try:
        request_file = parse_file(file)
    except ParseError as e:
        print_error(f"Failed to parse {file}: {e}")
        raise typer.Exit(code=1)

    if not request_file.requests:
        print_error(f"No requests found in {file}.")
        raise typer.Exit(code=1)

    effective_env = env_name or request_file.environment_name
    environment = load_environment(file.parent, effective_env)

    variables: dict[str, str] = {}
    if environment:
        variables = environment.variables
        if not quiet:
            make_console(stderr=True).print(f"[dim]Environment: {environment.name}[/]")

    if request_index is not None:
        if request_index < 0 or request_index >= len(request_file.requests):
            print_error(
                f"Request index {request_index} is out of range. "
                f"File has {len(request_file.requests)} request(s) "
                f"(0-{len(request_file.requests) - 1})."
            )
            raise typer.Exit(code=1)
        requests_to_run = [request_file.requests[request_index]]
    else:
        requests_to_run = request_file.requests

    token_provider = OAuth2TokenProvider()
    responses: dict[str, Response] = {}
    any_failed = False
    for request in requests_to_run:
        try:
            resolved = resolve_request(request, variables, responses)
        except Exception as e:
            any_failed = True
            print_error(f"Failed to resolve variables for '{request.name or request.url}': {e}")
            continue

        if not quiet:
            print_request_summary(resolved)

        try:
            response = execute_request(
                resolved,
                timeout=timeout,
                token_provider=token_provider,
                base_dir=file.parent,
            )
            if not quiet:
                print_response(response, show_headers=show_headers)
        except ConnectionError as e:
            print_error(str(e))
            raise typer.Exit(code=1)
        except Exception as e:
            print_error(f"Request failed: {e}")
            raise typer.Exit(code=1)

        assertions_passed = True
        if check_assertions and request.assertions:
            for assertion in request.assertions.assertions:
                try:
                    evaluate_assertion(assertion, response, response.elapsed_ms)
                    if not quiet:
                        make_console().print(f"  [green]✓[/] {assertion.raw_line}")
                except AssertionFailure as e:
                    assertions_passed = False
                    any_failed = True
                    make_console(stderr=True).print(f"  [red]✗[/] {e.message}")
                    if not quiet:
                        expected_lines = (
                            str(e.expected).splitlines() if e.expected is not None else [""]
                        )
                        actual_lines = str(e.actual).splitlines() if e.actual is not None else [""]
                        diff = "\n".join(
                            difflib.unified_diff(
                                expected_lines,
                                actual_lines,
                                fromfile="expected",
                                tofile="actual",
                                lineterm="",
                            )
                        )
                        make_console(stderr=True).print(f"    {diff}")

        if request.name and (not check_assertions or assertions_passed):
            responses[request.name] = response

    if any_failed:
        raise typer.Exit(code=1)


@app.command()
def validate(
    file: Path = typer.Argument(
        ...,
        help="Path to the .http file to validate.",
        exists=True,
        readable=True,
        dir_okay=False,
    ),
) -> None:
    """Validate a .http file for syntax and structure errors."""
    try:
        request_file = parse_file(file)
    except ParseError as e:
        print_error(f"Validation failed: {e}")
        raise typer.Exit(code=1)
    except FileNotFoundError as e:
        print_error(str(e))
        raise typer.Exit(code=1)

    if not request_file.requests:
        print_error("No valid requests found in the file.")
        raise typer.Exit(code=1)

    all_variables: dict[str, str] = {}
    environment = load_environment(file.parent, request_file.environment_name)
    if environment:
        all_variables = environment.variables

    unresolved: list[str] = []
    var_pattern = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")

    def collect_unresolved(text: str | None) -> None:
        if not text:
            return
        for match in var_pattern.finditer(text):
            var_name = match.group(1)
            if var_name not in all_variables:
                unresolved.append(var_name)

    for request in request_file.requests:
        collect_unresolved(request.url)
        for header in request.headers:
            collect_unresolved(header.name)
            collect_unresolved(header.value)
        if request.body:
            collect_unresolved(request.body.content)
        if request.oauth2:
            collect_unresolved(request.oauth2.token_url)
            collect_unresolved(request.oauth2.client_id)
            collect_unresolved(request.oauth2.client_secret)
            for scope in request.oauth2.scopes:
                collect_unresolved(scope)
        for part in request.multipart:
            collect_unresolved(part.name)
            collect_unresolved(part.value)
            collect_unresolved(part.file_path)
            collect_unresolved(part.content_type)

    console = make_console()
    console.print(f"[green]✓[/] {file} is valid")
    console.print(f"  {len(request_file.requests)} request(s) found")

    if request_file.environment_name:
        console.print(f"  Environment: {request_file.environment_name}")

    if unresolved:
        unique_unresolved = list(set(unresolved))
        console.print(f"\n[yellow]⚠[/] Unresolved variables ({len(unique_unresolved)}):")
        for var in sorted(unique_unresolved):
            console.print(f"  [yellow]{{{{{var}}}}}[/]")

    raise typer.Exit(code=0)


@app.command(name="test")
def test_cmd(
    file: Path = typer.Argument(
        ...,
        help="Path to a .http file or directory collection to execute.",
        exists=True,
        readable=True,
        dir_okay=True,
    ),
    env_name: str | None = typer.Option(
        None,
        "--env",
        "-e",
        help="Environment name (e.g., dev, staging, prod). Uses .env.{name}.",
    ),
    timeout: float = typer.Option(
        30.0,
        "--timeout",
        "-t",
        help="Request timeout in seconds.",
        min=1.0,
    ),
    junit_xml: Path | None = typer.Option(
        None,
        "--junit-xml",
        help="Write JUnit XML results to this file.",
    ),
    json_output: Path | None = typer.Option(
        None,
        "--json",
        help="Write JSON results to this file.",
    ),
) -> None:
    """Run one .http file or every .http file in a directory collection."""
    try:
        results, file_count = run_test_target(file, env_name=env_name, timeout=timeout)
    except ValueError as exc:
        print_error(str(exc))
        raise typer.Exit(code=1)

    for result in results:
        label = f"{result.source}::{result.name}" if result.source else result.name
        if result.passed:
            console = make_console()
            console.print(f"[green]PASS[/] {label} ({result.status_code})")
        else:
            assertion_messages = [
                assertion.message for assertion in result.assertions if assertion.message
            ]
            if result.error:
                message = (
                    f"{result.error}\n" + "\n".join(assertion_messages)
                    if assertion_messages
                    else result.error
                )
            else:
                message = (
                    "\n".join(assertion_messages) if assertion_messages else "Assertion failed"
                )
            make_console(stderr=True).print(f"[red]FAIL[/] {label}: {message}")

    if junit_xml:
        write_junit_report(junit_xml, results)
    if json_output:
        write_json_report(json_output, results)

    passed_count = sum(result.passed for result in results)
    failed_count = len(results) - passed_count
    make_console().print(
        f"[bold]{passed_count} passed, {failed_count} failed across {file_count} file(s)[/]"
    )

    if any(not result.passed for result in results):
        raise typer.Exit(code=1)


@app.command(name="format")
def format_cmd(
    file: Path = typer.Argument(
        ...,
        help="Path to the .http file to format.",
        exists=True,
        readable=True,
        dir_okay=False,
    ),
    check: bool = typer.Option(
        False,
        "--check",
        "-c",
        help="Check if file is formatted. Exit 0 if yes, 1 if not. Don't modify.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write formatted output to this file. Modifies in-place if omitted.",
    ),
) -> None:
    """Format a .http file for consistent style."""
    try:
        request_file = parse_file(file)
    except ParseError as e:
        print_error(f"Failed to parse {file}: {e}")
        raise typer.Exit(code=1)

    if not request_file.requests:
        print_error("No requests found to format.")
        raise typer.Exit(code=1)

    formatted_content = format_file(request_file)

    if check:
        original = file.read_text(encoding="utf-8")
        if original.strip() == formatted_content.strip():
            console = make_console()
            console.print("[green]✓[/] File is already formatted.")
        else:
            print_error("File needs formatting.")
            raise typer.Exit(code=1)
        return

    target = output or file
    target.write_text(formatted_content, encoding="utf-8")

    console = make_console()
    if output:
        console.print(f"[green]✓[/] Formatted output written to {output}")
    else:
        console.print(f"[green]✓[/] {file} formatted in-place")


@app.command()
def version() -> None:
    """Show the probeflow and implemented grammar versions."""
    console = make_console()
    console.print(f"probeflow {__version__} (spec {__spec_version__})")


if __name__ == "__main__":
    app()
