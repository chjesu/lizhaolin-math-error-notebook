#!/usr/bin/env python3
"""Extract a redacted, reviewable timeline from a Codex rollout JSONL file."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;\"']+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
)


def redact(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda match: (match.group(1) if match.lastindex else "") + "[REDACTED]", text)
    return text


def compact(value: Any, limit: int = 8000) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    value = redact(value)
    return value if len(value) <= limit else value[:limit] + "\n[TRUNCATED]"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("rollout", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--match", default="")
    args = parser.parse_args()
    matcher = re.compile(args.match, re.I) if args.match else None
    entries: list[str] = []
    meta: dict[str, Any] = {}

    with args.rollout.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            timestamp = item.get("timestamp", "")
            kind = item.get("type")
            payload = item.get("payload") or {}
            ptype = payload.get("type")
            body = ""
            label = ""
            if kind == "session_meta":
                meta = {
                    "id": payload.get("id"),
                    "parent_thread_id": payload.get("parent_thread_id"),
                    "cwd": payload.get("cwd"),
                    "originator": payload.get("originator"),
                    "model_provider": payload.get("model_provider"),
                    "source": payload.get("source"),
                }
                continue
            if kind == "event_msg" and ptype == "user_message":
                label, body = "USER", payload.get("message", "")
            elif kind == "event_msg" and ptype == "agent_message":
                label, body = "ASSISTANT", payload.get("message", "")
            elif kind == "response_item" and ptype in {"function_call", "custom_tool_call"}:
                name = payload.get("name", "unknown")
                label = f"TOOL {name}"
                body = payload.get("arguments") or payload.get("input") or ""
            else:
                continue
            body = compact(body)
            searchable = f"{label}\n{body}"
            if matcher and not matcher.search(searchable):
                continue
            entries.append(f"## {timestamp} — {label} — line {line_number}\n\n```text\n{body}\n```\n")

    header = "# Codex rollout audit\n\n" + "```json\n" + json.dumps(meta, ensure_ascii=False, indent=2) + "\n```\n\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(header + "\n".join(entries), encoding="utf-8")
    print(json.dumps({"entries": len(entries), "output": str(args.output), "meta": meta}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
