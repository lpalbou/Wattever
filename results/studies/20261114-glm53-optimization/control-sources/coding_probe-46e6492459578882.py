"""Generate and check a small coding task through the local gateway."""
import argparse
import ast
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "coding-probe.json"
MODEL = "qwen3.8-flash-next"
SAFE_BUILTINS = {"sorted": sorted, "list": list, "range": range, "len": len,
                 "min": min, "max": max, "enumerate": enumerate, "tuple": tuple,
                 "reversed": reversed, "zip": zip, "int": int, "abs": abs}
ALLOWED_NODES = {
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.Assign,
    ast.AnnAssign, ast.AugAssign, ast.For, ast.While, ast.If, ast.Break, ast.Continue,
    ast.Pass, ast.Expr, ast.Name, ast.Load, ast.Store, ast.Constant, ast.List, ast.Tuple,
    ast.Subscript, ast.Slice, ast.Call, ast.Attribute, ast.Compare, ast.BoolOp, ast.BinOp,
    ast.UnaryOp, ast.IfExp, ast.ListComp, ast.comprehension, ast.And, ast.Or, ast.Not,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.USub, ast.UAdd,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot,
}


def validate_code(code):
    if not isinstance(code, str) or len(code) > 20000:
        raise ValueError("code must be a string of at most 20000 characters")
    tree = ast.parse(code)
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise ValueError("expected exactly one top-level function")
    function = tree.body[0]
    if function.name != "merge_intervals" or function.decorator_list or function.returns or function.type_comment:
        raise ValueError("expected an undecorated merge_intervals function without annotations")
    if len(function.args.args) != 1 or function.args.posonlyargs or function.args.kwonlyargs or function.args.vararg or function.args.kwarg or function.args.defaults:
        raise ValueError("expected exactly one plain argument")
    if any(arg.annotation for arg in function.args.args):
        raise ValueError("annotations are not allowed")
    for node in ast.walk(tree):
        if type(node) not in ALLOWED_NODES:
            raise ValueError(f"disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.FunctionDef) and node is not function:
            raise ValueError("nested functions are not allowed")
        if isinstance(node, ast.Name) and ("__" in node.id or node.id in {"eval", "exec", "open", "compile", "globals", "locals", "getattr", "setattr", "delattr", "vars", "input", "help", "breakpoint"}):
            raise ValueError(f"disallowed name: {node.id}")
        if isinstance(node, ast.Attribute) and node.attr != "append":
            raise ValueError(f"disallowed attribute: {node.attr}")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id not in SAFE_BUILTINS:
                    raise ValueError(f"disallowed call: {node.func.id}")
            elif not (isinstance(node.func, ast.Attribute) and node.func.attr == "append"):
                raise ValueError("disallowed call")
            if node.keywords:
                raise ValueError("keyword arguments are not allowed")
    return tree


def generate():
    from openai import OpenAI
    import httpx
    client = OpenAI(base_url="http://127.0.0.1:8086/v1", api_key="local", timeout=600,
                    max_retries=0, _strict_response_validation=True)
    schema = {"type": "object", "properties": {"code": {"type": "string"}},
              "required": ["code"], "additionalProperties": False}
    messages = [{"role": "user", "content": "Write a Python function merge_intervals(intervals). Input is a list of [start, end] integer pairs, possibly unsorted. Return a new list of [start, end] pairs sorted by start, merging overlapping or touching intervals. Handle empty input, negative numbers and nested intervals. Do not mutate the input. Use no imports, decorators, annotations, globals, helper functions, dunder names, or attributes except list.append. Use only these builtins if needed: sorted, list, range, len, min, max, enumerate, tuple, reversed, zip, int, abs. Return exactly one function definition as the code string."}]
    start = time.perf_counter()
    response = client.chat.completions.create(model=MODEL, messages=messages, reasoning_effort="none",
        temperature=0, max_completion_tokens=768,
        response_format={"type": "json_schema", "json_schema": {"name": "coding_solution", "strict": True, "schema": schema}})
    elapsed = time.perf_counter() - start
    body = json.loads(response.choices[0].message.content)
    from jsonschema import validate
    validate(body, schema)
    report = {"model": MODEL, "request_seconds": round(elapsed, 4), "usage": response.usage.model_dump(mode="json") if response.usage else None,
              "finish_reason": response.choices[0].finish_reason, "code": body["code"]}
    try:
        status = httpx.get("http://127.0.0.1:8081/v1/status", timeout=5)
        status.raise_for_status()
        report["backend_last_timings"] = status.json().get("last_timings")
    except (httpx.HTTPError, ValueError) as exc:
        report["backend_status_error"] = str(exc)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved generated code and timings to {REPORT}")


def worker(code):
    tree = validate_code(code)
    namespace = {"__builtins__": SAFE_BUILTINS}
    exec(compile(tree, "<model-code>", "exec"), namespace)
    function = namespace["merge_intervals"]
    cases = [
        ("empty", [], []),
        ("touching", [[1, 2], [2, 5]], [[1, 5]]),
        ("nested", [[1, 10], [3, 4], [2, 8]], [[1, 10]]),
        ("negative", [[-5, -2], [-3, 1], [3, 4]], [[-5, 1], [3, 4]]),
        ("unsorted", [[8, 10], [1, 3], [2, 6], [15, 18]], [[1, 6], [8, 10], [15, 18]]),
        ("singletons and duplicates", [[0, 0], [3, 3], [0, 0]], [[0, 0], [3, 3]]),
        ("bridge", [[1, 2], [5, 7], [2, 5]], [[1, 7]]),
    ]
    results = []
    for name, data, expected in cases:
        original = [item[:] for item in data]
        try:
            actual = function(data)
            results.append({"name": name, "passed": actual == expected and data == original,
                            "expected": expected, "actual": actual, "input_unchanged": data == original})
        except Exception as exc:
            results.append({"name": name, "passed": False, "error": f"{type(exc).__name__}: {exc}"})
    return results


def check():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    code = report["code"]
    try:
        validate_code(code)
        result = subprocess.run([sys.executable, "-I", "-S", str(Path(__file__).resolve()), "--worker"],
                                input=json.dumps({"code": code}), text=True, capture_output=True, timeout=20)
        if result.returncode:
            raise RuntimeError(f"worker failed ({result.returncode}): {result.stderr[:2000]}")
        checks = json.loads(result.stdout)
        report["checks"] = checks
        report["check_passed"] = all(item["passed"] for item in checks)
    except (ValueError, SyntaxError, subprocess.TimeoutExpired, RuntimeError, json.JSONDecodeError) as exc:
        report["check_passed"] = False
        report["check_error"] = f"{type(exc).__name__}: {exc}"
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Coding checks: {'PASS' if report['check_passed'] else 'FAIL'}; results in {REPORT}")
    return 0 if report["check_passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--generate", action="store_true", help="send one coding inference request")
    group.add_argument("--check", action="store_true", help="validate and run saved code against test cases")
    group.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(json.load(sys.stdin)["code"])))
        return 0
    if args.generate:
        generate()
        return 0
    return check()


if __name__ == "__main__":
    sys.exit(main())
