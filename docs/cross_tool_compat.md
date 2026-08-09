# Cross-Tool Compatibility Guide

probeflow files are standard `.http` files that remain fully openable and
executable in both **VS Code REST Client** and **JetBrains HTTP Client**.
This document explains the compatibility contract and how probeflow-specific
extensions are handled.

## Verification status

The syntax contract is covered by the repository's VS Code and JetBrains-shaped
fixtures and parser round-trip tests. Runtime verification with the actual
clients is environment-dependent: the audited workspace has the VS Code CLI but
no REST Client extension installed, and no JetBrains HTTP client executable.
Those client-level checks remain a release-validation task; this document does
not treat the static fixture checks as proof that both editor clients ran the
files successfully.

## The Golden Rule

Every line that probeflow adds beyond the base `.http` format lives inside a
**comment**. VS Code REST Client and JetBrains HTTP Client silently ignore
comments they don't recognize, so probeflow `.http` files work unchanged in
both editors.

## Syntax Compatibility Matrix

| Feature                     | VS Code REST Client | JetBrains HTTP Client | probeflow    |
|-----------------------------|---------------------|-----------------------|------------|
| `GET/POST/…` request line   | ✅                  | ✅                    | ✅         |
| `Header: value`             | ✅                  | ✅                    | ✅         |
| Request body (blank line)   | ✅                  | ✅                    | ✅         |
| `###` separator             | ✅                  | ✅                    | ✅         |
| `# comment`                 | ✅ (ignored)        | ✅ (ignored)          | ✅ (parsed)|
| `// comment`                | ✅ (ignored)        | ✅ (ignored)          | ✅ (ignored)|
| `{{variable}}`              | ✅                  | ✅                    | ✅         |
| `### @name = <name>`        | Ignored¹            | Ignored¹              | ✅         |
| `### @env = <name>`         | Ignored¹            | Ignored¹              | ✅         |
| `### @assert` block         | Ignored¹            | Ignored¹              | ✅         |
| `### @before = <hook>`      | Ignored¹            | Ignored¹              | ✅         |
| `### @after = <hook>`       | Ignored¹            | Ignored¹              | ✅         |
| `HTTP/1.1` version suffix   | ✅                  | ✅                    | ✅         |

¹ These are probeflow-specific directives embedded in `###` comment lines.
Other tools see them as decorative section comments and skip them.

## How probeflow Directives Stay Invisible

probeflow directives use the `### @directive` pattern. This works because:

1. **`###`** is a valid separator/comment prefix in both REST Client and
   JetBrains. Both tools treat `### <text>` as a named separator or
   comment and display the text as a section heading.

2. **`# assertion lines`** inside `@assert` blocks are hash-comments that
   both tools silently ignore.

This means the following file works identically in all three tools:

```http
### @name = getUser
### @env = staging
GET https://api.example.com/users/1
Accept: application/json

### @assert
# status == 200
# body.$.name != null
# duration < 500ms

### @after = hooks.py:log_response

###

### Health check
GET https://api.example.com/health
```

- **VS Code REST Client**: Runs both requests. Ignores all `###` lines
  and `#` lines as comments.
- **JetBrains HTTP Client**: Same behavior — runs both requests, ignores
  comments.
- **probeflow**: Parses assertions, hooks, names, and env directives. The
  `test` command runs requests and evaluates `@assert` blocks as conditions.
  Hook-bearing files are refused because this release does not execute scripts.

## Assertion Syntax

Assertions live in `### @assert` blocks. Each assertion is a `#` comment
line with the format:

```
# <target> <operator> <expected>
```

### Targets

| Target           | Description                              |
|------------------|------------------------------------------|
| `status`         | HTTP status code (integer)               |
| `duration`       | Response time in milliseconds            |
| `body.$.<path>`  | JSONPath-like body field (dot-notation)   |
| `header.<name>`  | Response header value                    |

### Operators

| Operator | Meaning             |
|----------|---------------------|
| `==`     | Equal               |
| `!=`     | Not equal           |
| `<`      | Less than           |
| `>`      | Greater than        |
| `<=`     | Less than or equal  |
| `>=`     | Greater than or equal|
| `in`     | Value in list       |
| `contains` | String contains  |
| `matches`  | Regex match      |

### Literals

| Literal    | Example         |
|------------|-----------------|
| Integer    | `200`, `-42`    |
| Float      | `3.14`          |
| String     | `"hello world"` |
| Boolean    | `true`, `false` |
| Null       | `null`          |
| List       | `[200, 201]`    |
| Duration   | `500ms`         |

## Hook References

Hooks are Python functions that run before or after a request:

```http
### @before = hooks.py:setup_auth
### @after = hooks.py:save_token
GET https://api.example.com/login
```

The format is `file_path:function_name`. Hooks are opt-in and require
`--allow-scripts` to execute.

## Variable Resolution

Variables use the `{{name}}` syntax, resolved from:

1. `.env` files (`.env`, `.env.staging`, etc.)
2. System environment variables
3. Chained response values (Phase 1+)

## Testing in CI

```yaml
# GitHub Actions example
- name: Run API tests
  run: python -m probeflow.cli test tests/api.http --env ci --junit-xml api-results.xml
```

Files are the test — the `.http` format _is_ the test definition. No
separate config or mapping files needed.
