#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["chalkcompute==2.13.5"]
# ///
"""Deploy the factory layout agent (server, agent, solver and page) as a Chalk ScalingGroup.

The agent reaches Claude through the Chalk model gateway. Requirements on the target environment:
  - the Chalk router is enabled (`chalk router status`)
  - a secret holds a router API key (`chalk router api-key create`), CHALK_ROUTER_API_KEY by default

Credentials for the deploy itself come from `chalk config` in the current directory (or the
CHALK_* env vars):
    ./deploy.py [--env-id <environment>] [--name factory-layout-agent]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_FILES = [
    "factory.py",
    "solver.py",
    "agent.py",
    "server.py",
    "index.html",
    "app.js",
    "sample_run.json",
]


def load_chalk_env(env_id: str | None) -> None:
    if "CHALK_CLIENT_ID" not in os.environ:
        cfg = json.loads(subprocess.check_output(["chalk", "config", "--json"]))
        os.environ["CHALK_API_SERVER"] = cfg["apiServer"]["value"]
        os.environ["CHALK_CLIENT_ID"] = cfg["clientId"]["value"]
        os.environ["CHALK_CLIENT_SECRET"] = cfg["clientSecret"]["value"]
        os.environ.setdefault("CHALK_ENVIRONMENT_ID", cfg["environment"]["value"])
    if env_id:
        os.environ["CHALK_ENVIRONMENT_ID"] = env_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="factory-layout-agent")
    parser.add_argument("--env-id", default=None)
    parser.add_argument("--model", default="anthropic/claude-opus-5-5")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--key-secret", default="CHALK_ROUTER_API_KEY")
    parser.add_argument("--ready-timeout", type=float, default=900.0)
    args = parser.parse_args()

    load_chalk_env(args.env_id)
    from chalkcompute import Image, ReadinessProbe, ScalingGroup, Secret

    image = Image.debian_slim(python_version="3.12").pip_install(
        ["ortools==9.15.6755", "anthropic==1.13.0", "fastapi==0.116.1", "uvicorn[standard]==0.35.0"]
    )
    # The app files total more than the 32 KiB inline `copy` budget, so they ride a volume
    # mounted at /app when the replica starts.
    for name in APP_FILES:
        image = image.add_local_file(str(HERE / name), f"/app/{name}", strategy="volume")
    image = image.workdir("/app")

    sg = ScalingGroup(
        image=image,
        name=args.name,
        # The repair search runs CP-SAT with 8 worker threads for up to 3 s per failed check.
        cpu="2",
        memory="4Gi",
        env={
            # Not secrets: where the model gateway lives and which environment's router to use.
            "CHALK_API_SERVER": os.environ["CHALK_API_SERVER"],
            "CHALK_ENVIRONMENT_ID": os.environ["CHALK_ENVIRONMENT_ID"],
            "MODEL": args.model,
            "EFFORT": args.effort,
            "PORT": "8080",
            "ROUTER_API_KEY": Secret.from_chalk_env(args.key_secret),
        },
        entrypoint=["python", "/app/server.py"],
        port=8080,
        readiness_probe=ReadinessProbe.http("/status", period_seconds=5),
        # Runs stream from the replica that started them; one replica keeps the budgets global.
        min_replicas=1,
        max_replicas=1,
    )
    t0 = time.monotonic()
    try:
        sg.deploy(build_timeout=1200.0, ready_timeout=args.ready_timeout)
    finally:
        print(f"deploy returned after {time.monotonic() - t0:.0f}s")
    print("web_url:", sg.web_url)


if __name__ == "__main__":
    main()
