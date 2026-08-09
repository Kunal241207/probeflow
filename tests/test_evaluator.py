import json
import pytest
from probeflow.client import Response
from probeflow.models import Request

from probeflow.models import Assertion, AssertionTarget, AssertionOperator
from probeflow.evaluator import evaluate_assertion, AssertionFailure, _extract_jsonpath

def make_response(status_code=200, json_data=None, headers=None, text=""):
    return Response(
        status_code=status_code,
        status_text="OK",
        elapsed_ms=10.0,
        size_bytes=100,
        headers=headers or {},
        body=text,
        parsed_body=json_data,
        content_type="application/json" if json_data else "text/plain",
        url="https://example.com"
    )

def test_extract_jsonpath():
    data = {
        "user": {
            "id": 42,
            "roles": ["admin", "user"],
            "profile": {"name": "Alice"}
        },
        "tags": []
    }
    
    assert _extract_jsonpath(data, "$.user.id") == 42
    assert _extract_jsonpath(data, "$.user.roles[0]") == "admin"
    assert _extract_jsonpath(data, "$.user.roles[1]") == "user"
    assert _extract_jsonpath(data, "$.user.profile.name") == "Alice"
    assert _extract_jsonpath(data, "$.tags") == []
    
    # Invalid paths
    assert _extract_jsonpath(data, "user.id") is None
    assert _extract_jsonpath(data, "$.unknown") is None
    assert _extract_jsonpath(data, "$.user.roles[99]") is None
    assert _extract_jsonpath(data, "$.user.roles.invalid") is None
    assert _extract_jsonpath(data, "$.user.id.nested") is None

def test_status_assertion():
    resp = make_response(201)
    
    # Pass
    evaluate_assertion(Assertion(target=AssertionTarget.STATUS, operator=AssertionOperator.EQ, expected=201), resp, 100)
    
    # Fail
    with pytest.raises(AssertionFailure) as exc:
        evaluate_assertion(Assertion(target=AssertionTarget.STATUS, operator=AssertionOperator.EQ, expected=200), resp, 100)
    assert exc.value.expected == 200
    assert exc.value.actual == 201

def test_duration_assertion():
    resp = make_response(200)
    
    # Pass
    evaluate_assertion(Assertion(target=AssertionTarget.DURATION, operator=AssertionOperator.LT, expected=500), resp, 120.5)
    
    # Fail
    with pytest.raises(AssertionFailure):
        evaluate_assertion(Assertion(target=AssertionTarget.DURATION, operator=AssertionOperator.LT, expected=100), resp, 120.5)

def test_header_assertion():
    resp = make_response(headers={"content-type": "application/json", "x-custom": "val"})
    
    # Pass
    evaluate_assertion(Assertion(target=AssertionTarget.HEADER, path="Content-Type", operator=AssertionOperator.EQ, expected="application/json"), resp, 10)
    evaluate_assertion(Assertion(target=AssertionTarget.HEADER, path="X-Custom", operator=AssertionOperator.EQ, expected="val"), resp, 10)
    
    # Fail
    with pytest.raises(AssertionFailure):
        evaluate_assertion(Assertion(target=AssertionTarget.HEADER, path="Content-Type", operator=AssertionOperator.EQ, expected="text/html"), resp, 10)

def test_body_jsonpath_assertion():
    resp = make_response(json_data={"name": "Alice", "age": 30})
    
    # Pass
    evaluate_assertion(Assertion(target=AssertionTarget.BODY, path="$.name", operator=AssertionOperator.EQ, expected="Alice"), resp, 10)
    evaluate_assertion(Assertion(target=AssertionTarget.BODY, path="$.age", operator=AssertionOperator.GT, expected=25), resp, 10)
    
    # Fail
    with pytest.raises(AssertionFailure):
        evaluate_assertion(Assertion(target=AssertionTarget.BODY, path="$.name", operator=AssertionOperator.EQ, expected="Bob"), resp, 10)

def test_operators():
    resp = make_response(json_data={"val": 10, "text": "hello", "items": [1, 2, 3], "flag": True})
    
    # IN
    evaluate_assertion(Assertion(target=AssertionTarget.BODY, path="$.val", operator=AssertionOperator.IN, expected=[5, 10, 15]), resp, 10)
    with pytest.raises(AssertionFailure):
        evaluate_assertion(Assertion(target=AssertionTarget.BODY, path="$.val", operator=AssertionOperator.IN, expected=[1, 2]), resp, 10)
        
    # CONTAINS
    evaluate_assertion(Assertion(target=AssertionTarget.BODY, path="$.text", operator=AssertionOperator.CONTAINS, expected="ell"), resp, 10)
    evaluate_assertion(Assertion(target=AssertionTarget.BODY, path="$.items", operator=AssertionOperator.CONTAINS, expected=2), resp, 10)
    
    # MATCHES
    evaluate_assertion(Assertion(target=AssertionTarget.BODY, path="$.text", operator=AssertionOperator.MATCHES, expected="^he.*o$"), resp, 10)
    with pytest.raises(AssertionFailure):
        evaluate_assertion(Assertion(target=AssertionTarget.BODY, path="$.text", operator=AssertionOperator.MATCHES, expected="^[0-9]+$"), resp, 10)
        
    # EXISTS / NOT_EXISTS
    evaluate_assertion(Assertion(target=AssertionTarget.BODY, path="$.val", operator=AssertionOperator.EXISTS, expected=None, raw_line="$.val exists"), resp, 10)
    evaluate_assertion(Assertion(target=AssertionTarget.BODY, path="$.missing", operator=AssertionOperator.NOT_EXISTS, expected=None, raw_line="$.missing not exists"), resp, 10)
    
    # IS type
    evaluate_assertion(Assertion(target=AssertionTarget.BODY, path="$.text", operator=AssertionOperator.IS, expected="string"), resp, 10)
    evaluate_assertion(Assertion(target=AssertionTarget.BODY, path="$.val", operator=AssertionOperator.IS, expected="number"), resp, 10)
    evaluate_assertion(Assertion(target=AssertionTarget.BODY, path="$.flag", operator=AssertionOperator.IS, expected="boolean"), resp, 10)
    evaluate_assertion(Assertion(target=AssertionTarget.BODY, path="$.items", operator=AssertionOperator.IS, expected="array"), resp, 10)
