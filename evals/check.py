"""Check an agent transcript against the playbook cases.

    python evals/check.py <case-id> transcript.json

`transcript.json` is either a plain list of tool calls
(`[{"name": "get_status", "args": {...}}, ...]`), or Claude Code's
`--output-format stream-json` output (one JSON object per line; `tool_use`
blocks are picked out). The final assistant text may be given as
`{"calls": [...], "text": "..."}` so `confirm` can be checked.

Exit code 0 when the case passes; the reasons are printed otherwise.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CASES = json.loads((Path(__file__).parent / "cases.json").read_text(encoding="utf-8"))["cases"]


def load_transcript(path: str) -> tuple[list[dict], str]:
    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if isinstance(data, dict) and "calls" in data:
        return list(data["calls"]), str(data.get("text", ""))
    if isinstance(data, dict):  # a single stream-json event
        data = [data]
    calls, text = [], ""
    for item in data:
        if isinstance(item, dict) and "name" in item and "type" not in item:
            calls.append({"name": item["name"], "args": item.get("args") or item.get("input") or {}})
            continue
        message = item.get("message", item) if isinstance(item, dict) else {}
        for block in message.get("content", []) if isinstance(message, dict) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name", "")
                calls.append({"name": name.split("__")[-1], "args": block.get("input") or {}})
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
    return calls, text


def is_subsequence(expected: list[str], names: list[str]) -> bool:
    it = iter(names)
    return all(any(e == n for n in it) for e in expected)


def check(case: dict, calls: list[dict], text: str) -> list[str]:
    names = [c["name"] for c in calls]
    problems = []
    if not is_subsequence(case.get("expect", []), names):
        problems.append(f"expected {case['expect']} in order, got {names}")
    for name in case.get("forbid", []):
        if name in names:
            problems.append(f"must not call {name}")
    for name, limit in (case.get("max_calls") or {}).items():
        if names.count(name) > limit:
            problems.append(f"{name} called {names.count(name)} times, limit {limit}")
    for name, wanted in (case.get("args") or {}).items():
        got = next((c["args"] for c in calls if c["name"] == name), None)
        if got is None:
            problems.append(f"{name} was not called")
        else:
            for k, v in wanted.items():
                if got.get(k) != v:
                    problems.append(f"{name}.{k}: wanted {v!r}, got {got.get(k)!r}")
    if case.get("confirm") and "?" not in text:
        problems.append("the turn must end by asking for confirmation")
    return problems


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    case_id, path = sys.argv[1:]
    case = next((c for c in CASES if c["id"] == case_id), None)
    if case is None:
        print(f"unknown case {case_id}; known: {[c['id'] for c in CASES]}")
        return 2
    calls, text = load_transcript(path)
    problems = check(case, calls, text)
    for p in problems:
        print("FAIL", case_id, p)
    if not problems:
        print("PASS", case_id)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
