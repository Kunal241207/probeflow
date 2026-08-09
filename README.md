# probeflow

Pytest for HTTP APIs: a local CLI that runs Git-diffable `.http` request files
as an enforceable test suite. No accounts, cloud sync, GUI, or team workspace service.

## Overview

**probeflow** reads IntelliJ / VS Code REST Client request files, executes them,
checks explicit response assertions, and fails CI when the contract breaks.

## Installation

```bash
# From source (recommended for development)
pip install -e ".[dev]"

# Or install from PyPI
pip install probeflow
```

After installation the `probeflow` command is available in your PATH.

## Quick Start

Create a `.http` file:

```http
### @name = getUsers
GET https://api.example.com/users
Accept: application/json

###

### @name = createUser
POST https://api.example.com/users
Content-Type: application/json

{
    "name": "Alice",
    "email": "alice@example.com"
}
```

Run it:

```bash
probeflow run requests.http
probeflow test requests.http   # Execute as an enforceable test suite
```

## Commands

### `probeflow run`

Execute requests from a `.http` file interactively.

```bash
probeflow run requests.http                  # Run all requests
probeflow run requests.http --index 0        # Run one request by 0-based index
probeflow run requests.http --env dev        # Use .env.dev for variable substitution
probeflow run requests.http --headers        # Show response headers
probeflow run requests.http --timeout 60     # Set timeout in seconds (default: 30)
probeflow run requests.http --quiet          # Suppress output, show errors only
```

Response chaining works in `run` too — a named request's response is available
as `{{name.response.body.$.field}}` in subsequent requests within the same file.

### `probeflow test`

Execute requests in declaration order and evaluate every `### @assert` block.
Exits `0` only when every request completes and every assertion passes.

```bash
probeflow test tests/api.http
probeflow test tests/api.http --env staging
probeflow test tests/api.http --json results.json --junit-xml results.xml
```

### `probeflow validate`

Check a `.http` file for syntax errors without executing any requests.

```bash
probeflow validate requests.http
```

### `probeflow format`

Normalize a `.http` file for consistent style (method casing, header formatting,
separator spacing).

```bash
probeflow format requests.http               # Format in-place
probeflow format requests.http --check       # Check only — exit 0 if already formatted
probeflow format requests.http -o out.http   # Write to a different file
```

### `probeflow version`

Show the installed version and implemented grammar version.

```bash
probeflow version
```

## GitHub Actions

The `test` command's non-zero exit status makes a broken API produce a red check:

```yaml
name: API tests

on: [push, pull_request]

jobs:
  api:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[dev]"
      - name: Run API contract tests
        run: probeflow test tests/api.http --junit-xml api-results.xml
```

If `tests/api.http` contains `# status == 200` and the API returns 500, the
step exits 1 and GitHub marks the workflow with a red X.

## .http File Format

probeflow follows the IntelliJ / VS Code REST Client `.http` file format.
All probeflow-specific extensions (`### @assert`, `### @name`, etc.) use constructs
that non-probeflow tools treat as comments or separators, so files remain
compatible with both editors.

### Basic Request

```http
GET https://api.example.com/users
```

### Request with Headers and Body

```http
POST https://api.example.com/users
Content-Type: application/json
Accept: application/json
Authorization: Bearer token123

{
    "name": "Alice",
    "role": "admin"
}
```

### Multiple Requests

Separate requests with `###`:

```http
### @name = listUsers
GET https://api.example.com/users

###

### @name = createUser
POST https://api.example.com/users
Content-Type: application/json

{"name": "Bob"}
```

### Named Requests

```http
### @name = healthCheck
GET https://api.example.com/health
```

Names are used in test output and for response chaining.

### Comments

```http
// This is a comment
# This is also a comment
GET https://api.example.com/data
```

### HTTP Version

```http
GET https://api.example.com/data HTTP/1.1
```

### Script Hooks

`### @before` and `### @after` directives point to Python hook functions:

```http
### @name = createResource
### @before = hooks.py:sign_request
POST https://api.example.com/resources
Content-Type: application/json

{"name": "test-resource"}

### @after = hooks.py:verify_signature
```

Hook references are parsed and validated, but **not executed** in this release.
The test runner refuses files containing hooks rather than running arbitrary code
implicitly. A future executor will require an explicit `--allow-scripts` flag.

### Supported Methods

`GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS`

## Assertions

Add a `### @assert` block after any request to make `probeflow test` enforce
the response contract:

```http
### @name = getUser
GET https://api.example.com/users/1

### @assert
# status == 200
# body.$.name == "Alice"
# body.$.email is string
# body.$.roles is array
# body.$.address exists
# duration < 500ms
# header.Content-Type contains "application/json"
```

### Assertion Syntax

| Target | Example |
|---|---|
| Status code | `status == 200` · `status in [200, 201]` · `status < 300` |
| Body field | `body.$.name == "Alice"` · `body.$.count > 0` |
| Body type | `body.$.id is number` · `body.$.tags is array` |
| Body existence | `body.$.token exists` · `body.$.error not exists` |
| Body pattern | `body.$.email matches ".*@example\\.com"` |
| Header | `header.Content-Type contains "json"` · `header.X-Id exists` |
| Duration | `duration < 500ms` |

### Supported Operators

`==` `!=` `<` `>` `<=` `>=` `in` `contains` `matches` `exists` `not exists` `is`

### Type Names for `is`

`string` · `number` · `boolean` · `array` · `object`

## Response Chaining

Use a preceding named request's response as a variable in later requests:

```http
### @name = login
POST https://api.example.com/auth
Content-Type: application/json

{"username": "alice", "password": "{{auth_password}}"}

###

### @name = getProfile
GET https://api.example.com/me
Authorization: Bearer {{login.response.body.$.token}}

### @assert
# status == 200
# body.$.username == "alice"
```

### Chaining Reference Syntax

| Reference | Resolves to |
|---|---|
| `{{name.response.status}}` | HTTP status code as a string |
| `{{name.response.body.$.field}}` | JSONPath value from the response body |
| `{{name.response.headers.X-Request-Id}}` | Response header value |

Chaining references resolve in declaration order. Referencing a request that
hasn't run yet, or one that failed, is a hard error with a clear message.

## Environment Variables

### .env Files

Place a `.env` file (or `.env.<name>`) next to your `.http` file:

```env
# .env.dev
base_url=https://api.example.com
auth_token=my-secret-token
```

### Variable Substitution

Use `{{variable}}` syntax in URLs, headers, and bodies:

```http
### @env = dev
GET {{base_url}}/users
Authorization: Bearer {{auth_token}}
```

### Resolution Order

1. Chaining references (`{{name.response.*}}`) — resolved from prior responses
2. `.env.<name>` file variables (when `@env` is set)
3. Default `.env` / `.env.local` file variables
4. System environment variables (`os.environ`)
5. Left as-is (`{{name}}`) — visible in output so you know what's missing

## Output

`probeflow run` displays:

- **Status line** color-coded by range — green 2xx, yellow 3xx, red 4xx, bold red 5xx
- **Timing** in milliseconds and **response size** in human-readable bytes
- **JSON syntax highlighting** for `application/json` responses
- **Response headers** when `--headers` is passed

`probeflow test` displays per-request `PASS` / `FAIL` with the first failing
assertion message. Use `--json` or `--junit-xml` for machine-readable output.

## Development

```bash
# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Run the full test suite (coverage included)
python -m pytest

# Lint
ruff check .

# Format
ruff format .

# Check formatting without modifying
ruff format --check .
```

## Project Structure

```
probeflow/
├── probeflow/
│   ├── __init__.py       # Package metadata and version
│   ├── models.py         # Pydantic data models (AST)
│   ├── parser.py         # .http file lexer + grammar-driven parser
│   ├── client.py         # HTTP client (httpx wrapper)
│   ├── environment.py    # Variable substitution, .env loading, auth helpers
│   ├── evaluator.py      # Assertion evaluation engine
│   ├── formatter.py      # Rich terminal output
│   ├── test_runner.py    # Test suite loop, JUnit/JSON report writers
│   └── cli.py            # Typer CLI — run, test, validate, format, version
├── tests/
│   ├── conftest.py
│   ├── fixtures/          # .http fixture files + golden JSON snapshots
│   ├── test_parser.py
│   ├── test_parser_golden.py
│   ├── test_environment.py
│   ├── test_evaluator.py
│   ├── test_client.py
│   ├── test_cli.py
│   └── test_phase2.py    # Auth, multipart, response chaining
├── examples/
│   ├── simple.http
│   ├── requests.http
│   └── .env.dev
├── docs/
│   └── spec.md           # Formal .http grammar (EBNF)
├── pyproject.toml
└── README.md
```

## License

MIT
