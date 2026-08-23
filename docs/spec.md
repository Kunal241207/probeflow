# `.http` File Format Specification — probeflow v1

**Version**: 1.0  
**Date**: 2026-08-02  
**Status**: Implemented

This document defines the grammar for `.http` request files as consumed by probeflow.
The core format is compatible with VS Code REST Client and JetBrains HTTP Client.
probeflow-specific extensions (assertions, hooks, chaining) are encoded as comments
so that non-probeflow tools ignore them gracefully.

---

## 1. Notation

This spec uses EBNF with the following conventions:

- `"literal"` — terminal string (case-sensitive unless noted)
- `'literal'` — terminal string (case-sensitive)
- `/* comment */` — descriptive note
- `( ... )` — grouping
- `[ ... ]` — optional (0 or 1)
- `{ ... }` — repetition (0 or more)
- `( ... )+` — repetition (1 or more)
- `|` — alternation
- `UPPER_CASE` — named production rule
- `lower_case` — named terminal class

---

## 2. Lexical Elements

```ebnf
(* Characters and whitespace *)
newline         = "\n" | "\r\n" ;
ws              = " " | "\t" ;
blank_line      = { ws } , newline ;
any_char        = (* any Unicode character except newline *) ;
rest_of_line    = { any_char } ;

(* Identifiers and literals *)
identifier      = letter , { letter | digit | "_" | "-" } ;
letter          = "A".."Z" | "a".."z" ;
digit           = "0".."9" ;
integer         = digit , { digit } ;
float_number    = digit , { digit } , "." , digit , { digit } ;

(* Strings *)
quoted_string   = '"' , { any_char - '"' | '\\"' } , '"' ;

(* Variable references *)
variable_ref    = "{{" , { ws } , variable_path , { ws } , "}}" ;
variable_path   = identifier , { "." , variable_segment } ;
variable_segment = identifier | "response" , "." , response_accessor ;
response_accessor = "body" , "." , jsonpath_expr
                  | "headers" , "." , identifier
                  | "status" ;

(* JSON Path — v1 subset: dot-notation + array indexing only *)
jsonpath_expr   = "$" , { jsonpath_step } ;
jsonpath_step   = "." , identifier [ "[" , integer , "]" ]
                | "[" , integer , "]" ;
```

---

## 3. File Structure

```ebnf
http_file       = { file_element } , [ EOF ] ;

file_element    = request_block
                | separator_line
                | comment_line
                | blank_line ;

separator_line  = { ws } , "###" , [ { ws } , rest_of_line ] , newline ;
                  (* A line starting with ### that is NOT a directive line.
                     Used to separate request blocks. *)

(* A separator_line is distinguished from a directive_line by the
   absence of an @ sign after ###. Specifically, if the text after
   ### (trimmed) starts with @, it is a directive_line, not a separator. *)
```

---

## 4. Request Block

```ebnf
request_block   = { pre_request_meta }
                , request_line , newline
                , { header_line }
                , [ blank_line , body ]
                , { post_request_meta } ;

pre_request_meta  = comment_line
                  | name_directive
                  | env_directive
                  | oauth2_directive
                  | form_directive
                  | file_directive
                  | before_hook_directive
                  | blank_line ;

post_request_meta = assert_block
                  | after_hook_directive
                  | comment_line
                  | blank_line ;
```

### 4.1 Request Line

```ebnf
request_line    = { ws } , http_method , ws , { ws } , request_url
                , [ ws , { ws } , http_version ]
                , { ws } ;

http_method     = "GET" | "POST" | "PUT" | "PATCH" | "DELETE"
                | "HEAD" | "OPTIONS" ;
                  (* Case-insensitive on input; normalized to uppercase *)

request_url     = url_char , { url_char } ;
                  (* May contain {{variable}} references *)
url_char        = (* any non-whitespace character *) ;

http_version    = "HTTP/" , digit , "." , digit ;
```

### 4.2 Headers

```ebnf
header_line     = header_name , { ws } , ":" , { ws } , header_value , newline ;
header_name     = visible_char , { visible_char | "-" } ;
                  (* Must not start with # or // *)
header_value    = rest_of_line ;
                  (* May contain {{variable}} references *)
visible_char    = (* any printable non-whitespace character *) ;
```

### 4.3 Body

```ebnf
body            = body_line , { body_line | blank_line } ;
body_line       = (* any non-blank line that is not a separator_line
                     and not a directive_line *)
                , newline ;
                  (* Body extends until EOF or the next separator_line *)
```

The body is separated from headers by exactly one blank line.
If no blank line appears after headers, there is no body.
The body extends until:
- A `separator_line` (`###` that is not a directive)
- End of file

Body content may contain `{{variable}}` references which are resolved at execution time.

---

## 5. Comments

```ebnf
comment_line    = { ws } , comment_marker , rest_of_line , newline ;
comment_marker  = "//" | "#" ;
```

Comments are ignored during parsing (they do not contribute to the request).
However, probeflow assertion lines (§7) use `#` prefix — these are parsed by probeflow
but ignored by non-probeflow tools.

A line starting with `#` (but not `###`) inside a body section is treated as
body content, not a comment.

---

## 6. Directives

Directives are probeflow metadata lines. They use `### @` prefix so that non-probeflow
tools treat them as separator-like comment lines.

```ebnf
directive_line  = { ws } , "###" , { ws } , "@" , directive_name
                , { ws } , "=" , { ws } , directive_value
                , { ws } , newline ;

directive_name  = identifier ;
directive_value = rest_of_line ;
                  (* Trimmed of leading/trailing whitespace *)
```

### 6.1 `@name` — Request Name

```ebnf
name_directive  = { ws } , "###" , { ws } , "@name"
                , { ws } , "=" , { ws } , identifier
                , [ { ws } , "//" , rest_of_line ]
                , newline ;
```

Names a request for reference in chaining and output. Must be unique within a file.
Names are case-sensitive identifiers: `[a-zA-Z][a-zA-Z0-9_-]*`.

**Example:**
```http
### @name = getUser
GET https://api.example.com/users/1
```

### 6.2 `@env` — Environment Selector

```ebnf
env_directive   = { ws } , "###" , { ws } , "@env"
                , { ws } , "=" , { ws } , identifier
                , newline ;
```

Selects an environment file (`.env.<name>`) for variable resolution.
Should appear at the top of the file (before any request block).

**Example:**
```http
### @env = dev
```

### 6.3 `@before` / `@after` — Script Hooks

```ebnf
before_hook_directive = { ws } , "###" , { ws } , "@before"
                      , { ws } , "=" , { ws } , hook_ref
                      , newline ;

after_hook_directive  = { ws } , "###" , { ws } , "@after"
                      , { ws } , "=" , { ws } , hook_ref
                      , newline ;

hook_ref        = file_path , ":" , identifier ;
file_path       = (* relative path to a .py file, no spaces *) ;
```

Hook references point to a Python function in a sibling file.
Hooks are **only executed** when the `--allow-scripts` CLI flag is provided.
Without this flag, hook directives are parsed and validated but not executed.

**Example:**
```http
### @name = createResource
### @before = hooks.py:sign_request
POST https://api.example.com/resources
Content-Type: application/json

{"name": "test-resource"}

### @after = hooks.py:verify_signature
```

> **Security**: Hooks execute arbitrary Python code. The `--allow-scripts` flag
> is a security boundary. Files from untrusted sources (e.g., PRs) should never
> be run with `--allow-scripts` unless the hook code has been reviewed.

**Current implementation status:** the parser validates and preserves hook
references, but the current test runner refuses hook-bearing files because no
hook executor is shipped yet. A future executor must keep `--allow-scripts` as
an explicit opt-in and must never run hooks by default.

### 6.4 `@oauth2` — Client Credentials

OAuth2 client-credentials authentication is declared per request. Values may
use normal `{{variable}}` interpolation. Tokens are held only in memory for one
run and refreshed 30 seconds before their advertised expiry.

```ebnf
oauth2_directive = { ws } , "###" , { ws } , "@oauth2"
                 , { ws } , "=" , { ws } , "client-credentials"
                 , ws , token_url , ws , client_id , ws , client_secret
                 , { ws , scope } , newline ;
```

```http
### @oauth2 = client-credentials {{oauth_token_url}} {{client_id}} {{client_secret}} read write
GET https://api.example.com/me
```

### 6.5 `@form` / `@file` — Multipart Uploads

`@form` adds a text field and `@file` adds a file part. Paths are relative to
the `.http` file. Multipart requests must not set `Content-Type`; probeflow
uses the HTTP client's generated boundary.

```ebnf
form_directive = { ws } , "###" , { ws } , "@form"
               , { ws } , "=" , { ws } , identifier , "=" , rest_of_line , newline ;
file_directive = { ws } , "###" , { ws } , "@file"
               , { ws } , "=" , { ws } , identifier , "=" , file_path
               , [ ";type=" , media_type ] , newline ;
```

```http
### @form = title=Quarterly report
### @file = attachment=fixtures/report.pdf;type=application/pdf
POST https://api.example.com/uploads
```

---

## 7. Assertion Block

Assertions are encoded as comment-prefixed lines so non-probeflow tools see only comments.

```ebnf
assert_block    = assert_header , ( assertion_line )+ ;

assert_header   = { ws } , "###" , { ws } , "@assert"
                , { ws } , newline ;

assertion_line  = { ws } , "#" , { ws } , assertion_expr
                , { ws } , newline ;
```

### 7.1 Assertion Grammar

```ebnf
assertion_expr  = status_assertion
                | body_assertion
                | header_assertion
                | duration_assertion ;

(* --- Status assertions --- *)
status_assertion = "status" , { ws } , status_op , { ws } , status_value ;

status_op       = "==" | "!=" | "<" | ">" | "<=" | ">=" | "in" ;

status_value    = integer                                  (* status == 200 *)
                | "[" , integer , { "," , { ws } , integer } , "]" ;
                                                           (* status in [200, 201] *)

(* --- Body assertions --- *)
body_assertion  = "body" , "." , jsonpath_expr , { ws } , body_op ;

body_op         = comparison_op , { ws } , value_literal   (* body.$.name == "Alice" *)
                | "exists"                                  (* body.$.token exists *)
                | "not" , { ws } , "exists"                 (* body.$.token not exists *)
                | "is" , { ws } , type_name                 (* body.$.name is string *)
                | "contains" , { ws } , quoted_string       (* body.$.tags contains "admin" *)
                | "matches" , { ws } , quoted_string ;      (* body.$.email matches ".*@example.com" *)

comparison_op   = "==" | "!=" | "<" | ">" | "<=" | ">=" ;

value_literal   = quoted_string
                | integer
                | float_number
                | "true" | "false"
                | "null" ;

type_name       = "string" | "number" | "boolean"
                | "array" | "object" | "null" ;

(* --- Header assertions --- *)
header_assertion = "header" , "." , identifier , { ws } , header_op ;

header_op       = comparison_op , { ws } , quoted_string    (* header.Content-Type == "application/json" *)
                | "contains" , { ws } , quoted_string       (* header.Content-Type contains "json" *)
                | "exists"                                   (* header.X-Request-Id exists *)
                | "not" , { ws } , "exists" ;               (* header.X-Custom not exists *)

(* --- Duration assertions --- *)
duration_assertion = "duration" , { ws } , comparison_op , { ws }
                   , integer , "ms" ;                       (* duration < 500ms *)
```

### 7.2 Assertion Block Example

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

Non-probeflow tools see this as:
```
### @assert          ← treated as a separator with comment text
# status == 200     ← treated as a comment
# body.$.name ...   ← treated as a comment
...
```

---

## 8. Response Chaining

A request may reference the response of an earlier named request using the
`{{name.response.*}}` syntax within the existing `{{variable}}` template system.

```ebnf
chaining_ref    = "{{" , { ws } , identifier , ".response."
                , response_field , { ws } , "}}" ;

response_field  = "body" , "." , jsonpath_expr
                | "headers" , "." , identifier
                | "status" ;
```

### 8.1 Resolution Rules

1. Chaining references resolve **only within one `.http` file**. A directory
   collection runs files independently, so a named response never leaks across
   files.
2. Requests are evaluated in **declaration order** (top-to-bottom in the file).
3. A reference to a request that **hasn't run yet** is a **hard parse error**
   with a clear message — not a silent empty string.
4. A reference to a request that **failed** is a **hard runtime error** with a
   clear message.
5. The `identifier` in `{{identifier.response.*}}` must match an `@name` directive
   of a preceding request.

### 8.2 Chaining Example

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

---

## 9. Variable Resolution Order

When resolving a `{{name}}` reference:

1. **Chaining references**: If `name` matches `<request_name>.response.*`, resolve
   from the named request's captured response.
2. **Inline variables**: `### @key = value` directives within the same request block.
3. **Environment file**: Variables from `.env.<name>` or `.env` file.
4. **System environment**: `os.environ` lookup.
5. **Unresolved**: Left as-is (`{{name}}`) — produces a warning, not an error,
   unless in `test` mode where unresolved variables are an error.

---

## 10. Collections

`probeflow test` accepts either a `.http` file or a directory. A directory is
searched recursively for `.http` files in lexical path order. Each file loads
its own environment and has its own response-chain scope. Results are aggregated
into one pass/fail summary, and any file failure makes the command exit with 1.

```text
probeflow test requests/
```

## 11. Cross-Tool Compatibility

### 10.1 Design Principle

All probeflow-specific syntax is encoded using constructs that non-probeflow tools
interpret as either:

- **Comments** (`#` or `//` prefixed lines) — ignored silently
- **Separators** (`###` lines) — treated as request block delimiters
- **Template variables** (`{{...}}`) — treated as unresolved variables (warning, not error)

### 10.2 Compatibility Matrix

| Construct | VS Code REST Client | JetBrains HTTP Client | probeflow |
|-----------|--------------------|-----------------------|---------|
| `### @name = x` | Separator (text ignored) | Separator (text ignored) | Name directive |
| `### @assert` | Separator | Separator | Assert block header |
| `# status == 200` | Comment | Comment | Assertion line |
| `### @before = f:fn` | Separator | Separator | Hook directive |
| `{{x.response.body.$.y}}` | Unresolved variable (warning) | Unresolved variable (warning) | Chaining reference |

### 10.3 What Must Never Happen

- A `.http` file produced by probeflow must **never** produce a parse error in
  VS Code REST Client or JetBrains HTTP Client.
- probeflow directives must **never** be mistaken for request content (headers,
  body, URL) by other tools.

## 12. CI-Safe Output

The CLI writes ANSI color only to interactive terminals. It disables color when
either `--no-color` is passed or `NO_COLOR` is present with a non-empty value;
an empty `NO_COLOR` value does not disable color. This follows
<https://no-color.org/>. CI workflows should use `NO_COLOR=1` for stable logs.

---

## 13. Error Reporting

All parse errors must include:

- **File name** (or `<stdin>` for string input)
- **Line number** (1-indexed)
- **Column number** (1-indexed)
- **Human-readable message** describing what was expected vs. what was found
- **No bare stack traces** — all Python exceptions must be caught and converted
  to structured `ParseError` objects

### 13.1 Error Message Format

```
<filename>:<line>:<column>: error: <message>
```

Example:
```
requests.http:5:1: error: Unsupported HTTP method 'INVALID'. Expected one of: GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS
requests.http:12:3: error: Invalid assertion syntax: 'status = 200'. Did you mean 'status == 200'?
```

---

## 14. Versioning

This specification is versioned. The version number appears at the top of this document.

- **Major version** changes indicate breaking changes to the grammar.
- **Minor version** changes indicate additions that are backward-compatible.

probeflow includes the spec version it implements in its `version` command
output (for example, `probeflow 0.1.0 (spec 1.0)`).

---

## Appendix A: Complete Example File

```http
### @env = staging

### @name = login
POST {{base_url}}/auth
Content-Type: application/json

{
    "username": "alice",
    "password": "{{auth_password}}"
}

### @assert
# status == 200
# body.$.token is string
# body.$.token exists
# duration < 2000ms

###

### @name = getProfile
### @before = hooks.py:add_request_id
GET {{base_url}}/me
Authorization: Bearer {{login.response.body.$.token}}

### @assert
# status == 200
# body.$.username == "alice"
# body.$.email is string
# header.Content-Type contains "application/json"

###

### @name = updateProfile
PUT {{base_url}}/me
Authorization: Bearer {{login.response.body.$.token}}
Content-Type: application/json

{
    "display_name": "Alice Wonderland"
}

### @assert
# status == 200
# body.$.display_name == "Alice Wonderland"
```
