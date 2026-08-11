#!/usr/bin/env python3
"""Internal client for an already-running local Qwen Gradio service."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="internal local VLM client")
    parser.add_argument("--url", required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-new-tokens", type=int, default=1536)
    args = parser.parse_args()

    if not args.image.is_file():
        raise SystemExit(f"image not found: {args.image}")
    if not args.prompt.is_file():
        raise SystemExit(f"prompt not found: {args.prompt}")

    from gradio_client import Client, handle_file

    prompt = args.prompt.read_text(encoding="utf-8")
    prompt += f"\n\n本次 source_sha256：{args.source_sha256}"
    client = Client(args.url, verbose=False)
    result = client.predict(
        image=handle_file(str(args.image.resolve())),
        prompt=prompt,
        max_new_tokens=args.max_new_tokens,
        api_name="/answer",
    )
    response = result[0] if isinstance(result, (list, tuple)) else result
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(str(response), encoding="utf-8")
    print(
        json.dumps(
            {"status": "ok", "output": str(args.output.resolve())},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
