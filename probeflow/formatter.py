"""Terminal output formatting using Rich."""

from __future__ import annotations

import json
import os
import sys

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from probeflow.client import Response
from probeflow.models import Request

_force_no_color = False


def configure_output(no_color: bool = False) -> None:
    """Configure process-wide CLI color policy."""
    global _force_no_color
    _force_no_color = no_color


def should_use_color(stream=None) -> bool:
    """Check whether ANSI styling is enabled and stream is interactive.

    Follows the NO_COLOR spec (disabled only when variable is non-empty).
    """
    if _force_no_color or os.environ.get("NO_COLOR"):
        return False
    output = stream or sys.stdout
    return bool(getattr(output, "isatty", lambda: False)())


def make_console(stderr: bool = False) -> Console:
    stream = sys.stderr if stderr else sys.stdout
    color = should_use_color(stream)
    return Console(stderr=stderr, no_color=not color, color_system="standard" if color else None)


def _status_color(code: int) -> str:
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
    c = console or make_console()
    name = f" [{request.name}]" if request.name else ""
    c.print(f"\n[bold cyan]{request.method.value}[/] {request.url}{name}")


def print_response(
    response: Response,
    show_headers: bool = False,
    console: Console | None = None,
) -> None:
    c = console or make_console()
    color = _status_color(response.status_code)

    c.print(
        f"  [{color}]HTTP/1.1 {response.status_code} {response.status_text}[/]", highlight=False
    )
    c.print(f"  [dim]{response.elapsed_ms}ms[/] [dim]{_human_size(response.size_bytes)}[/]")

    if show_headers:
        c.print()
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="dim")
        table.add_column()
        for name, value in response.headers.items():
            table.add_row(name + ":", value)
        c.print(table)

    if response.body:
        c.print()
        if response.content_type and "json" in response.content_type.lower():
            _print_json_body(response.body, c)
        else:
            c.print(response.body, highlight=False)


def _print_json_body(body: str, console: Console) -> None:
    try:
        parsed = json.loads(body)
        formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
        console.print(Syntax(formatted, "json", theme="monokai", word_wrap=True))
    except (json.JSONDecodeError, ValueError):
        console.print(body, highlight=False)


def _human_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def print_error(message: str, console: Console | None = None) -> None:
    c = console or make_console(stderr=True)
    c.print(f"[bold red]Error:[/] {message}")
