#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx"]
# ///
"""Ask the deployed agent for a production line and print what it and the solver do.

./example.py https://<web_url> ["Set up a manufacturing line for a chalkboard."]
./example.py https://<web_url> --verify my_layout.json
"""

from __future__ import annotations

import json
import sys

import httpx


def design(base: str, prompt: str) -> None:
    with httpx.stream("POST", f"{base}/api/design", json={"prompt": prompt}, timeout=600) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            e = json.loads(line)
            if e["type"] == "tool":
                print(f"-> {e['name']}: {e['summary']}")
            elif e["type"] == "verdict":
                if e["structure_errors"]:
                    print(f"round {e['round']}: malformed: {e['structure_errors']}")
                    continue
                print(
                    f"round {e['round']}: valid={e['valid']} "
                    + f"violations={len(e['violations'])} rules={e['constraints']} "
                    + f"solver={e['solve_ms']} ms"
                )
                for v in e["violations"][:5]:
                    print("   ", v["message"])
            elif e["type"] == "done":
                print(e["summary"])
                print(f"{e['checks']} checks, {e['elapsed_s']} s")
            elif e["type"] == "error":
                print("error:", e["message"])


def verify(base: str, path: str) -> None:
    r = httpx.post(f"{base}/api/verify", content=open(path, "rb").read(), timeout=60)
    r.raise_for_status()
    v = r.json()
    print(f"valid={v['valid']} rules={v['constraints']} solver={v['solve_ms']} ms")
    for msg in v["structure_errors"] + [x["message"] for x in v["violations"]]:
        print("   ", msg)


if __name__ == "__main__":
    base = sys.argv[1].rstrip("/")
    if len(sys.argv) > 3 and sys.argv[2] == "--verify":
        verify(base, sys.argv[3])
    else:
        design(
            base,
            sys.argv[2] if len(sys.argv) > 2 else "Set up a manufacturing line for a chalkboard.",
        )
