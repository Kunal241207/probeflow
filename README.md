# probeflow

Pytest for HTTP APIs: a local CLI that runs Git-diffable `.http` request files
as an enforceable test suite. It has no accounts, cloud sync, GUI, or team
workspace service.

## Overview

**probeflow** reads IntelliJ / VS Code REST Client request files, executes them,
checks explicit response assertions, and fails CI when the contract breaks.

## Installation

```bash
# From source
pip install -e ".[dev]"

# Or install directly
pip install probeflow
```

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
probeflow test requests.http              # Execute as an enforceable test suite
```

## Commands

### `probeflow run`

Execute requests from a `.http` file.

```bash
probeflow run requests.http                    # Run all requests
probeflow run requests.http --index 0          # Run specific request (0-based)
probeflow run requests.http --env dev          # Use .env.dev file
probeflow run requests.http --headers          # Show response headers
probeflow run requests.http --timeout 60       # Set timeout (default: 30s)
probeflow run requests.http --quiet            # Suppress output
```

`run` is the interactive request command. Omit `--index` to run every request;
use `--index` to select one request. For CI-enforced assertions, use `test`.

### `probeflow test`

Execute requests in declaration order and evaluate each `### @assert` block.
The command exits 0 only when every request executes and every assertion passes.

```bash
probeflow test tests/api.http
probeflow test tests/api.http --json results.json --junit-xml results.xml
```

Response values from a preceding named request can be chained into later
requests, for example `{{login.response.body.$.token}}`.

### GitHub Actions

The test command's non-zero exit status makes a broken API produce a red check:

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
          python-version: "3.12"
      - run: python -m pip install -e ".[dev]"
      - name: Run API contract tests
        run: python -m probeflow.cli test tests/api.http --junit-xml api-results.xml
```

If `tests/api.http` contains `# status == 200` and the API returns 500, the
test step exits 1 and GitHub marks the workflow with a red X.

### `probeflow validate`

Check a `.http` file for syntax errors without executing requests.

```bash
probeflow validate requests.http
```

### `probeflow format`

Normalize and format a `.http` file for consistent style.

```bash
probeflow format requests.http                 # Format in-place
probeflow format requests.http --check         # Check without modifying (exit 0 if formatted)
probeflow format requests.http -o output.http  # Write to a different file
```

### `probeflow version`

Show the current version.

```bash
probeflow version
```

## .http File Format

probeflow follows the IntelliJ / VS Code REST Client `.http` file format:

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

### Multiple Requests (separated by `###`)

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

Use `### @name = <identifier>` to give requests a name:

```http
### @name = healthCheck
GET https://api.example.com/health
```

### Comments

Lines starting with `//` or `#` are treated as comments:

```http
// This is a comment
# This is also a comment
GET https://api.example.com/data
```

### HTTP Version

You can specify an HTTP version (optional):

```http
GET https://api.example.com/data HTTP/1.1
```

### Script hooks and safety

`### @before` and `### @after` hook references are parsed as part of the file
grammar, but this release does not execute arbitrary hook code. The test runner
refuses a file containing hooks rather than running them implicitly. Any future
hook executor must require an explicit `--allow-scripts` flag; hook files are a
supply-chain risk and should be reviewed before execution.

### Supported Methods

`GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS`

## Environment Variables

### .env Files

Place a `.env` file (or `.env.<name>`) next to your `.http` file:

```env
# .env.dev
base_url = https://api.example.com
auth_token = my-secret-token
```

### Variable Substitution

Use `{{variable}}` syntax in URLs, headers, and bodies:

```http
### @env = dev
GET {{base_url}}/users
Authorization: Bearer {{auth_token}}
```

Variables are resolved in this order:
1. `.env.<name>` file (if `@env` is specified)
2. Default `.env` file
3. System environment variables
4. Left as-is if unresolved

## Output

probeflow displays:

- **Status line** with color-coded status codes (green for 2xx, yellow for 3xx, red for 4xx/5xx)
- **Timing** in milliseconds
- **Response size** in human-readable format
- **JSON syntax highlighting** for JSON responses
- **Response headers** (with `--headers` flag)

## Development

```bash
# Run tests
python -m pytest

# Run with coverage
python -m pytest --cov=probeflow

# Lint
ruff check .
ruff format --check .
```

## Project Structure

```
probeflow/
├── probeflow/
│   ├── __init__.py      # Package metadata
│   ├── models.py        # Pydantic data models
│   ├── parser.py        # .http file parser
│   ├── client.py        # HTTP client (httpx wrapper)
│   ├── environment.py   # Variable substitution
│   ├── formatter.py     # Rich output formatting
│   └── cli.py           # Typer CLI interface
├── tests/
│   ├── test_parser.py
│   ├── test_environment.py
│   ├── test_client.py
│   └── test_cli.py
├── examples/
│   ├── requests.http
│   ├── simple.http
│   └── .env.dev
├── pyproject.toml
└── README.md
```

## License

MIT
