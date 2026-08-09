"""Terminal output formatting using Rich.

Provides colorful, structured display of requests, responses, validation
results, and other CLI output.
"""

from __future__ import annotations

import json

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from probeflow.client import Response
from probeflow.models import ParseError, Request


def _make_console() -> Console:
    """Create a standard output console."""
    return Console()


def _make_error_console() -> Console:
    """Create a stderr console for errors."""
    return Console(stderr=True)


def _status_color(code: int) -> str:
    """Return a Rich color for an HTTP status code."""
    if 200 <= code < 300:
        return "green"
    elif 300 <= code < 400:
        return "yellow"
    elif 400 <= code < 500:
        return "red"
    elif 500 <= code < 600:
        return "bold red"
    return "white"


def print_request_summary(request: Request, console: Console | None = None) -> None:
    """Print a compact summary of the request being sent.

    Shows method, URL, and request name if available.
    """
    console = console or _make_console()
    name = f" [{request.name}]" if request.name else ""
    console.print(f"\n[bold cyan]{request.method.value}[/] {request.url}{name}")


def print_response(
    response: Response,
    show_headers: bool = False,
    console: Console | None = None,
) -> None:
    """Print a formatted response with status, timing, and body.

    Args:
        response: The response to display.
        show_headers: Whether to include response headers.
        console: Optional Rich console instance.
    """
    console = console or _make_console()

    color = _status_color(response.status_code)

    # Status bar
    console.print(
        f"  [{color}]HTTP/1.1 {response.status_code} {response.status_text}[/]",
        highlight=False,
    )
    console.print(f"  [dim]{response.elapsed_ms}ms[/] [dim]{_human_size(response.size_bytes)}[/]")

    # Response headers
    if show_headers:
        console.print()
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="dim")
        table.add_column()
        for name, value in response.headers.items():
            table.add_row(name + ":", value)
        console.print(table)

    # Response body
    if response.body:
        console.print()
        if response.content_type and "json" in response.content_type.lower():
            _print_json_body(response.body, console)
        else:
            console.print(response.body, highlight=False)


def _print_json_body(body: str, console: Console) -> None:
    """Print a JSON body with syntax highlighting."""
    try:
        parsed = json.loads(body)
        formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
        syntax = Syntax(formatted, "json", theme="monokai", word_wrap=True)
        console.print(syntax)
    except (json.JSONDecodeError, ValueError):
        console.print(body, highlight=False)


def _human_size(size_bytes: int) -> str:
    """Format a byte count into a human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def print_error(message: str, console: Console | None = None) -> None:
    """Print an error message to stderr in red."""
    console = console or _make_error_console()
    console.print(f"[bold red]Error:[/] {message}")


def print_validation_error(
    error: ParseError,
    filename: str,
    console: Console | None = None,
) -> None:
    """Print a validation error with file context."""
    console = console or _make_error_console()
    console.print(f"[bold red]Validation Error in {filename}:[/] {error}")


def print_parse_errors(
    errors: list[tuple[int, str]],
    filename: str,
    console: Console | None = None,
) -> None:
    """Print multiple parse errors."""
    console = console or _make_error_console()
    console.print(f"[bold red]Parse Errors in {filename}:[/]")
    for line, msg in errors:
        console.print(f"  Line {line}: {msg}")


def print_no_env_warning(filename: str, console: Console | None = None) -> None:
    """Print a warning about missing environment variables."""
    console = console or _make_console()
    console.print(
        f"[yellow]Warning:[/] No .env file found for '{filename}'. "
        "Variables may be unresolved.",
    )


def print_unresolved_variables(
    variables: list[str],
    console: Console | None = None,
) -> None:
    """Print a list of unresolved variable names."""
    console = console or _make_error_console()
    for var in variables:
        console.print(f"  [yellow]{{{{{var}}}}}[/]")
