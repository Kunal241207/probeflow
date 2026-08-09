"""Parser for .http request files.

Implements the formal grammar defined in docs/spec.md.
Uses a single-pass line scanner (lexer) that classifies each line into a
token type, followed by a grammar-driven parser that builds the AST.

Parse errors always report file, line, column, and a human-readable message.
"""

from __future__ import annotations

import re
from enum import Enum, auto
from pathlib import Path
from typing import NamedTuple

from probeflow.models import (
    SUPPORTED_METHODS,
    Assertion,
    AssertBlock,
    AssertionOperator,
    AssertionTarget,
    Header,
    HookRef,
    HTTPMethod,
    ParseError,
    Request,
    RequestBody,
    RequestFile,
    SourceSpan,
)


# ═══════════════════════════════════════════════════════════════════════════
# Lexer — line-by-line token classification
# ═══════════════════════════════════════════════════════════════════════════

class TokenType(Enum):
    """Classification of a single line in a .http file."""

    SEPARATOR = auto()       # ### (without @ directive)
    DIRECTIVE = auto()       # ### @name = ..., ### @env = ..., ### @assert, etc.
    COMMENT = auto()         # // ... or # ...
    REQUEST_LINE = auto()    # METHOD URL [HTTP/x.y]
    HEADER = auto()          # Key: Value
    BLANK = auto()           # Empty / whitespace-only line
    BODY_LINE = auto()       # Anything else (body content)
    ASSERTION_LINE = auto()  # # <assertion_expr> (inside an assert block)


class Token(NamedTuple):
    """A classified line from the input."""

    type: TokenType
    text: str
    line: int       # 1-indexed line number
    col: int        # 1-indexed column of first non-whitespace (or 1 if blank)


# Regex patterns for line classification
_REQUEST_LINE_RE = re.compile(
    r"^(\s*)"
    r"(\w+)\s+"
    r"(\S+?)"
    r"(?:\s+HTTP/([\d.]+))?"
    r"\s*$",
    re.IGNORECASE,
)

_HEADER_RE = re.compile(
    r"^(\s*)"
    r"([A-Za-z][\w-]*)"
    r"\s*:\s*"
    r"(.*?)"
    r"\s*$",
)

_SEPARATOR_RE = re.compile(r"^(\s*)###\s*(.*?)\s*$")

_DIRECTIVE_RE = re.compile(
    r"^(\s*)###\s*@(\w+)"
    r"(?:\s*=\s*(.*?))?"
    r"\s*$",
    re.IGNORECASE,
)

_COMMENT_HASH_RE = re.compile(r"^(\s*)#(?!##)\s?(.*?)$")
_COMMENT_SLASH_RE = re.compile(r"^(\s*)//(.*?)$")

_BLANK_RE = re.compile(r"^\s*$")


def _col_of(line: str) -> int:
    """Return 1-indexed column of the first non-whitespace character."""
    stripped = line.lstrip()
    if not stripped:
        return 1
    return len(line) - len(stripped) + 1


def _tokenize(content: str) -> list[Token]:
    """Classify every line of input into tokens.

    This is the lexer pass. It does NOT validate grammar — it only
    classifies line shapes. The parser consumes these tokens.
    """
    lines = content.split("\n")
    tokens: list[Token] = []

    for lineno_0, raw_line in enumerate(lines):
        lineno = lineno_0 + 1
        col = _col_of(raw_line)

        # 1. Blank line
        if _BLANK_RE.match(raw_line):
            tokens.append(Token(TokenType.BLANK, raw_line, lineno, col))
            continue

        # 2. Directive line: ### @name = value
        dm = _DIRECTIVE_RE.match(raw_line)
        if dm:
            tokens.append(Token(TokenType.DIRECTIVE, raw_line, lineno, col))
            continue

        # 3. Separator line: ### (without @directive)
        sm = _SEPARATOR_RE.match(raw_line)
        if sm:
            tokens.append(Token(TokenType.SEPARATOR, raw_line, lineno, col))
            continue

        # 4. Comment: // ...
        if _COMMENT_SLASH_RE.match(raw_line):
            tokens.append(Token(TokenType.COMMENT, raw_line, lineno, col))
            continue

        # 5. Comment: # ... (single hash, not ###)
        if _COMMENT_HASH_RE.match(raw_line):
            tokens.append(Token(TokenType.COMMENT, raw_line, lineno, col))
            continue

        # 6. Request line: METHOD URL [HTTP/x.y]
        rm = _REQUEST_LINE_RE.match(raw_line)
        if rm:
            method = rm.group(2).upper()
            if method in SUPPORTED_METHODS:
                tokens.append(Token(TokenType.REQUEST_LINE, raw_line, lineno, col))
                continue

        # 7. Header line: Key: Value
        hm = _HEADER_RE.match(raw_line)
        if hm:
            tokens.append(Token(TokenType.HEADER, raw_line, lineno, col))
            continue

        # 8. Everything else is a body line
        tokens.append(Token(TokenType.BODY_LINE, raw_line, lineno, col))

    return tokens


# ═══════════════════════════════════════════════════════════════════════════
# Assertion expression parser
# ═══════════════════════════════════════════════════════════════════════════

_ASSERTION_STATUS_RE = re.compile(
    r"^status\s+"
    r"(==|!=|<|>|<=|>=|in)\s+"
    r"(.+)$"
)

_ASSERTION_BODY_RE = re.compile(
    r"^body\.\$(\.[^\s]+)?\s+"
    r"(==|!=|<|>|<=|>=|contains|matches|exists|not\s+exists|is|in)\s*"
    r"(.*?)$"
)

_ASSERTION_HEADER_RE = re.compile(
    r"^header\.([A-Za-z][\w-]*)\s+"
    r"(==|!=|contains|exists|not\s+exists)\s*"
    r"(.*?)$"
)

_ASSERTION_DURATION_RE = re.compile(
    r"^duration\s+"
    r"(==|!=|<|>|<=|>=)\s+"
    r"(\d+)ms$"
)


def _parse_value_literal(text: str) -> tuple[str, object]:
    """Parse a value literal and return (type_hint, value).

    Supports: quoted strings, integers, floats, true, false, null, lists.
    """
    text = text.strip()

    if not text:
        return ("empty", None)

    # Quoted string
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return ("string", text[1:-1].replace('\\"', '"'))

    # Boolean
    if text == "true":
        return ("bool", True)
    if text == "false":
        return ("bool", False)

    # Null
    if text == "null":
        return ("null", None)

    # List (for status in [200, 201])
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return ("list", [])
        items = [_parse_value_literal(x.strip()) for x in inner.split(",")]
        return ("list", [v for _, v in items])

    # Float
    if "." in text:
        try:
            return ("float", float(text))
        except ValueError:
            pass

    # Integer
    try:
        return ("int", int(text))
    except ValueError:
        pass

    return ("unknown", text)


def _parse_assertion_expr(
    text: str, lineno: int, col: int, filename: str
) -> Assertion:
    """Parse a single assertion expression line.

    Returns an Assertion model or raises ParseError.
    """
    text = text.strip()
    span = SourceSpan(start_line=lineno, start_col=col, end_line=lineno, end_col=col + len(text))

    # --- status assertions ---
    m = _ASSERTION_STATUS_RE.match(text)
    if m:
        op_str = m.group(1)
        _, value = _parse_value_literal(m.group(2).strip())
        return Assertion(
            target=AssertionTarget.STATUS,
            operator=AssertionOperator(op_str),
            expected=value,
            raw_line=text,
            span=span,
        )

    # --- duration assertions ---
    m = _ASSERTION_DURATION_RE.match(text)
    if m:
        op_str = m.group(1)
        value = int(m.group(2))
        return Assertion(
            target=AssertionTarget.DURATION,
            operator=AssertionOperator(op_str),
            expected=value,
            raw_line=text,
            span=span,
        )

    # --- body assertions ---
    m = _ASSERTION_BODY_RE.match(text)
    if m:
        path = "$" + (m.group(1) or "")
        op_str = re.sub(r"\s+", " ", m.group(2).strip())
        raw_expected = m.group(3).strip()

        if op_str in ("exists", "not exists"):
            return Assertion(
                target=AssertionTarget.BODY,
                path=path,
                operator=AssertionOperator(op_str),
                raw_line=text,
                span=span,
            )

        if op_str == "is":
            return Assertion(
                target=AssertionTarget.BODY,
                path=path,
                operator=AssertionOperator.IS,
                expected=raw_expected,
                raw_line=text,
                span=span,
            )

        _, value = _parse_value_literal(raw_expected)
        return Assertion(
            target=AssertionTarget.BODY,
            path=path,
            operator=AssertionOperator(op_str),
            expected=value,
            raw_line=text,
            span=span,
        )

    # --- header assertions ---
    m = _ASSERTION_HEADER_RE.match(text)
    if m:
        header_name = m.group(1)
        op_str = re.sub(r"\s+", " ", m.group(2).strip())
        raw_expected = m.group(3).strip()

        if op_str in ("exists", "not exists"):
            return Assertion(
                target=AssertionTarget.HEADER,
                path=header_name,
                operator=AssertionOperator(op_str),
                raw_line=text,
                span=span,
            )

        _, value = _parse_value_literal(raw_expected)
        return Assertion(
            target=AssertionTarget.HEADER,
            path=header_name,
            operator=AssertionOperator(op_str),
            expected=value,
            raw_line=text,
            span=span,
        )

    # --- unknown assertion ---
    raise ParseError(
        f"Invalid assertion syntax: '{text}'. "
        "Expected: status|body.$.<path>|header.<Name>|duration <op> <value>",
        line=lineno,
        column=col,
        filename=filename,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Parser — grammar-driven AST builder
# ═══════════════════════════════════════════════════════════════════════════

class _Parser:
    """Stateful parser that consumes a token list and produces a RequestFile.

    Implements the grammar from docs/spec.md.
    """

    def __init__(self, tokens: list[Token], filename: str) -> None:
        self._tokens = tokens
        self._filename = filename
        self._pos = 0
        self._env_name: str | None = None
        self._requests: list[Request] = []

    # -- Token access helpers -----------------------------------------------

    def _peek(self) -> Token | None:
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return None

    def _advance(self) -> Token:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def _at_end(self) -> bool:
        return self._pos >= len(self._tokens)


    # -- Grammar productions ------------------------------------------------

    def parse(self) -> RequestFile:
        """Top-level: http_file = { file_element } ."""
        while not self._at_end():
            tok = self._peek()
            if tok is None:
                break

            if tok.type == TokenType.SEPARATOR:
                self._advance()
            elif tok.type == TokenType.DIRECTIVE:
                self._parse_file_level_directive()
            elif tok.type == TokenType.COMMENT:
                self._advance()
            elif tok.type == TokenType.BLANK:
                self._advance()
            elif tok.type == TokenType.REQUEST_LINE:
                self._parse_request_block()
            elif tok.type == TokenType.BODY_LINE:
                # A non-request, non-comment, non-header, non-blank line
                # at the top level — likely an invalid method.
                self._error_invalid_request_line(tok)
            elif tok.type == TokenType.HEADER:
                # Header without a preceding request line
                self._advance()  # skip orphaned header
            else:
                self._advance()

        return RequestFile(
            filename=self._filename,
            requests=self._requests,
            environment_name=self._env_name,
        )

    def _parse_file_level_directive(self) -> None:
        """Handle a directive that appears outside a request block.

        Only @env is valid at file level. @name at file level starts the
        pre-request-meta of the next request block.
        """
        tok = self._peek()
        if tok is None:
            return

        directive_name, directive_value = self._extract_directive(tok)

        if directive_name == "env":
            self._env_name = directive_value
            self._advance()
        elif directive_name in ("name", "before"):
            # This is a pre-request directive — parse as request block
            self._parse_request_block()
        elif directive_name in ("after", "assert"):
            # These should only appear after a request block
            self._advance()  # skip (will be orphaned)
        else:
            # Unknown directive — skip
            self._advance()

    def _parse_request_block(self) -> None:
        """Parse a complete request block including pre/post metadata."""
        name: str | None = None
        before_hook: HookRef | None = None
        after_hook: HookRef | None = None
        block_start_line: int = self._peek().line if self._peek() else 1

        # --- Pre-request metadata ---
        while not self._at_end():
            tok = self._peek()
            if tok is None:
                break

            if tok.type == TokenType.DIRECTIVE:
                d_name, d_value = self._extract_directive(tok)
                if d_name == "name":
                    name = d_value
                    self._advance()
                elif d_name == "before":
                    before_hook = self._parse_hook_ref(d_value, tok)
                    self._advance()
                elif d_name == "env":
                    # @env inside a block — set file-level env
                    self._env_name = d_value
                    self._advance()
                else:
                    break
            elif tok.type in (TokenType.COMMENT, TokenType.BLANK):
                self._advance()
            elif tok.type == TokenType.REQUEST_LINE:
                break
            elif tok.type == TokenType.SEPARATOR:
                self._advance()
            else:
                break

        # --- Request line ---
        tok = self._peek()
        if tok is None or tok.type != TokenType.REQUEST_LINE:
            return  # No request line found — skip block

        req_tok = self._advance()
        method, url, http_version, _req_span = self._parse_request_line_token(req_tok)

        # --- Headers ---
        headers: list[Header] = []
        while not self._at_end():
            tok = self._peek()
            if tok is None:
                break

            if tok.type == TokenType.HEADER:
                headers.append(self._parse_header_token(self._advance()))
            elif tok.type == TokenType.COMMENT:
                self._advance()
            elif tok.type == TokenType.BLANK:
                break  # blank line = transition to body
            elif tok.type == TokenType.BODY_LINE:
                raise ParseError(
                    f"Expected a header (Key: Value) or a blank line, got: '{tok.text.strip()}'",
                    line=tok.line,
                    column=tok.col,
                    filename=self._filename,
                )
            else:
                break

        # --- Body ---
        body_lines: list[str] = []
        body_start: int | None = None
        body_end: int | None = None

        if not self._at_end() and self._peek() and self._peek().type == TokenType.BLANK:
            self._advance()  # consume the blank separator

            while not self._at_end():
                tok = self._peek()
                if tok is None:
                    break
                if tok.type in (TokenType.SEPARATOR, TokenType.DIRECTIVE):
                    break
                # Check if this is an assertion-start comment (### @assert)
                if tok.type == TokenType.DIRECTIVE:
                    break

                self._advance()
                body_lines.append(tok.text)
                if body_start is None:
                    body_start = tok.line
                body_end = tok.line

        body: RequestBody | None = None
        if body_lines:
            content = "\n".join(body_lines).strip()
            if content:
                content_type = self._detect_content_type(headers)
                body = RequestBody(
                    content=content,
                    content_type=content_type,
                    span=SourceSpan(
                        start_line=body_start or req_tok.line,
                        start_col=1,
                        end_line=body_end or req_tok.line,
                        end_col=1,
                    ) if body_start else None,
                )

        # --- Post-request metadata (assertions, @after) ---
        assertions: AssertBlock | None = None
        while not self._at_end():
            tok = self._peek()
            if tok is None:
                break

            if tok.type == TokenType.DIRECTIVE:
                d_name, d_value = self._extract_directive(tok)
                if d_name == "assert":
                    assertions = self._parse_assert_block()
                elif d_name == "after":
                    after_hook = self._parse_hook_ref(d_value, tok)
                    self._advance()
                elif d_name == "name":
                    # This belongs to the next request — don't consume
                    break
                else:
                    break
            elif tok.type == TokenType.COMMENT:
                self._advance()
            elif tok.type == TokenType.BLANK:
                self._advance()
            elif tok.type == TokenType.SEPARATOR:
                break
            else:
                break

        # --- Build request ---
        # Extract inline variables from pre-request directives
        env_variables: dict[str, str] = {}

        block_end_line = body_end or (headers[-1].span.end_line if headers and headers[-1].span else req_tok.line)

        request = Request(
            method=method,
            url=url,
            http_version=http_version,
            headers=headers,
            body=body,
            name=name,
            environment_variables=env_variables,
            assertions=assertions,
            before_hook=before_hook,
            after_hook=after_hook,
            span=SourceSpan(
                start_line=block_start_line,
                start_col=1,
                end_line=block_end_line,
                end_col=1,
            ),
        )
        self._requests.append(request)

    def _parse_assert_block(self) -> AssertBlock:
        """Parse ### @assert followed by # assertion lines."""
        header_tok = self._advance()  # consume ### @assert
        assertions: list[Assertion] = []
        block_start = header_tok.line

        while not self._at_end():
            tok = self._peek()
            if tok is None:
                break

            if tok.type == TokenType.BLANK:
                self._advance()
                continue

            if tok.type == TokenType.COMMENT:
                # Check if this is a # assertion line (not //)
                line_stripped = tok.text.strip()
                if line_stripped.startswith("#") and not line_stripped.startswith("##"):
                    # Extract the assertion text after the # marker
                    m = _COMMENT_HASH_RE.match(tok.text)
                    if m:
                        assertion_text = m.group(2).strip()
                        if assertion_text:
                            assertion = _parse_assertion_expr(
                                assertion_text,
                                tok.line,
                                tok.col + len(tok.text) - len(tok.text.lstrip()) + 2,
                                self._filename,
                            )
                            assertions.append(assertion)
                    self._advance()
                    continue
                elif line_stripped.startswith("//"):
                    # // comment inside assert block — skip
                    self._advance()
                    continue

            # Anything else (separator, directive, request line) ends the block
            break

        return AssertBlock(
            assertions=assertions,
            span=SourceSpan(
                start_line=block_start,
                start_col=1,
                end_line=assertions[-1].span.end_line if assertions and assertions[-1].span else block_start,
                end_col=1,
            ),
        )

    def _parse_hook_ref(self, value: str, tok: Token) -> HookRef:
        """Parse a hook reference like 'hooks.py:sign_request'."""
        if ":" not in value:
            raise ParseError(
                f"Invalid hook reference: '{value}'. "
                "Expected format: file.py:function_name",
                line=tok.line,
                column=tok.col,
                filename=self._filename,
            )
        file_path, func_name = value.rsplit(":", 1)
        return HookRef(
            file_path=file_path.strip(),
            function_name=func_name.strip(),
            span=SourceSpan(
                start_line=tok.line,
                start_col=tok.col,
                end_line=tok.line,
                end_col=tok.col + len(tok.text),
            ),
        )

    # -- Token extraction helpers -------------------------------------------

    def _extract_directive(self, tok: Token) -> tuple[str, str]:
        """Extract (directive_name, directive_value) from a directive token."""
        m = _DIRECTIVE_RE.match(tok.text)
        if not m:
            return ("", "")
        name = m.group(2).lower()
        value = (m.group(3) or "").strip()
        return (name, value)

    def _parse_request_line_token(
        self, tok: Token
    ) -> tuple[HTTPMethod, str, str | None, SourceSpan]:
        """Extract method, URL, HTTP version, and span from a request line token."""
        m = _REQUEST_LINE_RE.match(tok.text)
        if not m:
            raise ParseError(
                f"Invalid request line: '{tok.text.strip()}'",
                line=tok.line,
                column=tok.col,
                filename=self._filename,
            )

        method_str = m.group(2).upper()
        url = m.group(3)
        http_version = f"HTTP/{m.group(4)}" if m.group(4) else None

        if method_str not in SUPPORTED_METHODS:
            raise ParseError(
                f"Unsupported HTTP method: '{method_str}'. "
                f"Supported methods: {', '.join(SUPPORTED_METHODS)}",
                line=tok.line,
                column=tok.col,
                filename=self._filename,
            )

        span = SourceSpan(
            start_line=tok.line,
            start_col=tok.col,
            end_line=tok.line,
            end_col=tok.col + len(tok.text.rstrip()),
        )
        return HTTPMethod(method_str), url, http_version, span

    def _parse_header_token(self, tok: Token) -> Header:
        """Extract a Header from a header token."""
        m = _HEADER_RE.match(tok.text)
        if not m:
            raise ParseError(
                f"Invalid header: '{tok.text.strip()}'",
                line=tok.line,
                column=tok.col,
                filename=self._filename,
            )

        return Header(
            name=m.group(2).strip(),
            value=m.group(3).strip(),
            span=SourceSpan(
                start_line=tok.line,
                start_col=tok.col,
                end_line=tok.line,
                end_col=tok.col + len(tok.text.rstrip()),
            ),
        )

    def _detect_content_type(self, headers: list[Header]) -> str | None:
        """Extract Content-Type from parsed headers."""
        for h in headers:
            if h.name.lower() == "content-type":
                return h.value.strip().split(";")[0].strip()
        return None

    def _error_invalid_request_line(self, tok: Token) -> None:
        """Raise a clear error for an unrecognized line at the top level."""
        # Check if it looks like a request line with an invalid method
        parts = tok.text.strip().split(None, 1)
        if len(parts) >= 2 and parts[0].isalpha():
            raise ParseError(
                f"Unsupported HTTP method: '{parts[0].upper()}'. "
                f"Supported methods: {', '.join(SUPPORTED_METHODS)}",
                line=tok.line,
                column=tok.col,
                filename=self._filename,
            )
        # Generic error for unrecognized content
        raise ParseError(
            f"Unexpected content: '{tok.text.strip()[:60]}'. "
            "Expected a request line (METHOD URL), comment, or separator (###)",
            line=tok.line,
            column=tok.col,
            filename=self._filename,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

def parse_file(filepath: str | Path) -> RequestFile:
    """Parse a .http file into a RequestFile object.

    Args:
        filepath: Path to the .http file.

    Returns:
        A RequestFile containing all parsed requests.

    Raises:
        ParseError: If the file cannot be parsed.
        FileNotFoundError: If the file does not exist.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    content = path.read_text(encoding="utf-8")

    return parse_string(content, filename=path.name)


def parse_string(content: str, filename: str = "<input>") -> RequestFile:
    """Parse .http file content from a string.

    Args:
        content: The raw text content of a .http file.
        filename: Optional filename for error messages.

    Returns:
        A RequestFile containing all parsed requests.
    """
    tokens = _tokenize(content)
    parser = _Parser(tokens, filename)
    return parser.parse()


# ═══════════════════════════════════════════════════════════════════════════
# Formatter — round-trip .http output
# ═══════════════════════════════════════════════════════════════════════════

def format_request(request: Request) -> str:
    """Format a Request object back into .http file syntax.

    Round-trips all constructs: name, hooks, request line, headers, body,
    and assertion blocks.
    """
    lines: list[str] = []

    if request.name:
        lines.append(f"### @name = {request.name}")

    if request.before_hook:
        lines.append(
            f"### @before = {request.before_hook.file_path}:{request.before_hook.function_name}"
        )

    if request.environment_variables:
        for key, value in request.environment_variables.items():
            lines.append(f"### @{key} = {value}")

    # Build request line
    request_line = f"{request.method.value} {request.url}"
    if request.http_version:
        request_line += f" {request.http_version}"
    lines.append(request_line)

    # Headers
    for header in request.headers:
        lines.append(f"{header.name}: {header.value}")

    # Body
    if request.body:
        lines.append("")
        lines.append(request.body.content)

    # Assertion block
    if request.assertions and request.assertions.assertions:
        lines.append("")
        lines.append("### @assert")
        for assertion in request.assertions.assertions:
            lines.append(f"# {assertion.raw_line}")

    # After hook
    if request.after_hook:
        lines.append(
            f"### @after = {request.after_hook.file_path}:{request.after_hook.function_name}"
        )

    return "\n".join(lines)


def format_file(request_file: RequestFile) -> str:
    """Format an entire RequestFile back into .http syntax."""
    request_blocks = [format_request(request) for request in request_file.requests]
    formatted_requests = "\n\n###\n\n".join(request_blocks)

    if request_file.environment_name:
        prefix = f"### @env = {request_file.environment_name}"
        formatted = f"{prefix}\n\n{formatted_requests}" if formatted_requests else prefix
    else:
        formatted = formatted_requests

    return formatted + "\n" if formatted else ""
