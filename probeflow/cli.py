"""CLI interface for probeflow using Typer.

Provides the main commands:
  - run: Execute requests from a .http file
  - validate: Check a .http file for errors
  - format: Normalize and format a .http file
"""

from __future__ import annotations

import re
from pathlib import Path

import typer
from rich.console import Console

from probeflow import __spec_version__, __version__
from probeflow.client import execute_request
from probeflow.environment import (
    load_environment,
    resolve_request,
)
from probeflow.formatter import (
    print_error,
    print_request_summary,
    print_response,
)
from probeflow.models import ParseError
from probeflow.parser import format_file, parse_file
from probeflow.test_runner import run_test_suite, write_json_report, write_junit_report

app = typer.Typer(
    name="probeflow",
    help="Pytest for HTTP APIs using .http request files.",
    no_args_is_help=True,
    add_completion=True,
)

err_console = Console(stderr=True)


def app_entry() -> None:
    """Entry point for the CLI."""
    app()


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
) -> None:
    """Execute one or more HTTP requests from a .http file.

    Example:
        probeflow run requests.http
        probeflow run requests.http --index 0 --env dev
        probeflow run requests.http --headers
    """
    try:
        request_file = parse_file(file)
    except ParseError as e:
        print_error(f"Failed to parse {file}: {e}")
        raise typer.Exit(code=1)

    if not request_file.requests:
        print_error(f"No requests found in {file}.")
        raise typer.Exit(code=1)

    # Load environment
    env_dir = file.parent
    effective_env = env_name or request_file.environment_name
    environment = load_environment(env_dir, effective_env)

    variables: dict[str, str] = {}
    if environment:
        variables = environment.variables
        if not quiet:
            err_console.print(f"[dim]Environment: {environment.name}[/]")

    # Determine which requests to run
    if request_index is not None:
        if not (0 <= request_index < len(request_file.requests)):
            print_error(
                f"Index {request_index} is out of range. "
                f"File has {len(request_file.requests)} request(s) (0-based)."
            )
            raise typer.Exit(code=1)
        requests_to_run = [request_file.requests[request_index]]
    else:
        requests_to_run = request_file.requests

    responses: dict = {}
    for request in requests_to_run:
        try:
            resolved = resolve_request(request, variables, responses)
        except Exception as e:
            print_error(f"Failed to resolve variables for '{request.name or request.url}': {e}")
            continue

        if not quiet:
            print_request_summary(resolved)

        try:
            response = execute_request(resolved, timeout=timeout)
            if not quiet:
                print_response(response, show_headers=show_headers)
        except ConnectionError as e:
            print_error(str(e))
            raise typer.Exit(code=1)
        except Exception as e:
            print_error(f"Request failed: {e}")
            raise typer.Exit(code=1)

        if request.name:
            responses[request.name] = response


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
    """Validate a .http file for syntax and structure errors.

    Reports issues without executing any requests.

    Example:
        probeflow validate requests.http
    """
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

    # Check for unresolved variables
    all_variables: dict[str, str] = {}
    env_dir = file.parent
    effective_env = request_file.environment_name
    environment = load_environment(env_dir, effective_env)
    if environment:
        all_variables = environment.variables

    unresolved: list[str] = []
    var_pattern = re.compile(r"\{\{\s*(\w+)\s*\}\}")

    for request in request_file.requests:
        # Check URL
        for match in var_pattern.finditer(request.url):
            var_name = match.group(1)
            if var_name not in all_variables:
                unresolved.append(var_name)

        # Check headers
        for header in request.headers:
            for match in var_pattern.finditer(header.value):
                var_name = match.group(1)
                if var_name not in all_variables:
                    unresolved.append(var_name)

        # Check body
        if request.body:
            for match in var_pattern.finditer(request.body.content):
                var_name = match.group(1)
                if var_name not in all_variables:
                    unresolved.append(var_name)

    # Report results
    console = Console()
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
        help="Path to the .http test file to execute.",
        exists=True,
        readable=True,
        dir_okay=False,
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
    """Run requests as an enforceable API test suite."""
    try:
        request_file = parse_file(file)
    except ParseError as exc:
        print_error(f"Failed to parse {file}: {exc}")
        raise typer.Exit(code=1)

    if not request_file.requests:
        print_error(f"No requests found in {file}.")
        raise typer.Exit(code=1)

    environment = load_environment(file.parent, env_name or request_file.environment_name)
    variables = environment.variables if environment else {}
    results = run_test_suite(request_file.requests, variables, timeout=timeout)

    for result in results:
        if result.passed:
            console = Console()
            console.print(f"[green]PASS[/] {result.name} ({result.status_code})")
        else:
            failed_messages = [a.message for a in result.assertions if not a.passed and a.message]
            message = result.error or (
                failed_messages[0] if failed_messages else "Assertion failed"
            )
            err_console.print(f"[red]FAIL[/] {result.name}: {message}")

    if junit_xml:
        write_junit_report(junit_xml, results)
    if json_output:
        write_json_report(json_output, results)

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
    """Format a .http file for consistent style.

    Normalizes method casing, header formatting, and separator spacing.

    Example:
        probeflow format requests.http
        probeflow format requests.http --check
        probeflow format requests.http -o formatted.http
    """
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
            console = Console()
            console.print("[green]✓[/] File is already formatted.")
        else:
            print_error("File needs formatting.")
            raise typer.Exit(code=1)
        return

    # Write output
    target = output or file
    target.write_text(formatted_content, encoding="utf-8")

    console = Console()
    if output:
        console.print(f"[green]✓[/] Formatted output written to {output}")
    else:
        console.print(f"[green]✓[/] {file} formatted in-place")


@app.command()
def version() -> None:
    """Show the probeflow and implemented grammar versions."""
    console = Console()
    console.print(f"probeflow {__version__} (spec {__spec_version__})")


if __name__ == "__main__":
    app()
