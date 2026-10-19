"""Factory layout agent: Claude designs a production line, CP-SAT proves the layout valid.

    POST /api/design   {"prompt": "..."}   -> NDJSON stream of agent and solver events
    POST /api/verify   <layout JSON>       -> the solver's verdict on a layout you wrote
    GET  /api/site                         -> the building, equipment catalog and presets
    GET  /api/sample                       -> a recorded run, replayed by the page on load

Claude is reached through the Chalk model gateway with a router API key from a Chalk secret.
"""

from __future__ import annotations

import asyncio
import collections
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, AsyncIterator

import anthropic
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

import agent
import factory
import solver

HERE = Path(__file__).resolve().parent
API_SERVER = os.environ.get("CHALK_API_SERVER", "https://api.chalk.ai").rstrip("/")
ROUTER_BASE_URL = os.environ.get("ROUTER_BASE_URL", f"{API_SERVER}/v1/router")
MODEL = os.environ.get("MODEL", "anthropic/claude-opus-5-5")
EFFORT = os.environ.get("EFFORT", "medium")

# The demo is public and unauthenticated, and client IPs are not visible behind the gateway, so
# abuse is bounded replica-wide: concurrent runs, runs per hour, and request sizes.
MAX_CONCURRENT_RUNS = int(os.environ.get("MAX_CONCURRENT_RUNS", "2"))
MAX_RUNS_PER_HOUR = int(os.environ.get("MAX_RUNS_PER_HOUR", "40"))
MAX_VERIFY_PER_MINUTE = 120
MAX_PROMPT_CHARS = 300
MAX_LAYOUT_BYTES = 64 * 1024

app = FastAPI()
_run_slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
_run_times: collections.deque[float] = collections.deque()
_verify_times: collections.deque[float] = collections.deque()


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(
        base_url=ROUTER_BASE_URL,
        api_key=os.environ["ROUTER_API_KEY"],
        default_headers={"X-Chalk-Env-Id": os.environ["CHALK_ENVIRONMENT"]},
        timeout=180.0,
    )


def _take(window: collections.deque[float], limit: int, seconds: float) -> bool:
    now = time.monotonic()
    while window and now - window[0] > seconds:
        window.popleft()
    if len(window) >= limit:
        return False
    window.append(now)
    return True


class DesignRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=MAX_PROMPT_CHARS)


class _Cancelled(Exception):
    pass


@app.get("/")
async def index() -> HTMLResponse:
    return HTMLResponse((HERE / "index.html").read_text())


@app.get("/app.js")
async def app_js() -> HTMLResponse:
    return HTMLResponse((HERE / "app.js").read_text(), media_type="text/javascript")


@app.get("/status")
async def status() -> JSONResponse:
    return JSONResponse({"ok": True, "model": MODEL})


@app.get("/api/site")
async def site() -> JSONResponse:
    return JSONResponse(
        {
            "site": factory.site_description(),
            "equipment": factory.equipment_list(),
            "presets": factory.PRESETS,
            "families": solver.FAMILIES,
            "model": MODEL,
        }
    )


@app.get("/api/sample")
async def sample() -> JSONResponse:
    path = HERE / "sample_run.json"
    if not path.exists():
        raise HTTPException(404, "no recorded run")
    return JSONResponse(json.loads(path.read_text()))


@app.post("/api/verify")
async def verify(request: Request) -> JSONResponse:
    body = await request.body()
    if len(body) > MAX_LAYOUT_BYTES:
        raise HTTPException(413, "layout too large")
    if not _take(_verify_times, MAX_VERIFY_PER_MINUTE, 60.0):
        raise HTTPException(429, "the verifier is busy; try again in a minute")
    try:
        layout = json.loads(body)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"invalid JSON: {e}") from e
    result = await asyncio.to_thread(solver.verify, layout)
    if not result["valid"] and not result["structure_errors"] and result["layout"]:
        result["repair"] = await asyncio.to_thread(solver.repair, result["layout"], 3.0)
    return JSONResponse(result)


@app.post("/api/design")
async def design(req: DesignRequest, request: Request) -> StreamingResponse:
    if _run_slots.locked():
        raise HTTPException(
            429,
            "Both design slots are busy. Watch the recorded run, or try " + "again in a minute.",
        )
    if not _take(_run_times, MAX_RUNS_PER_HOUR, 3600.0):
        raise HTTPException(
            429,
            "The demo has hit its hourly budget of live runs. Watch the "
            + "recorded run, or try again later.",
        )
    await _run_slots.acquire()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    cancelled = threading.Event()

    def emit(event: dict[str, Any]) -> None:
        if cancelled.is_set():
            raise _Cancelled
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def work() -> None:
        try:
            emit({"type": "start", "model": MODEL, "effort": EFFORT, "prompt": req.prompt})
            agent.run(agent.RunConfig(_client(), MODEL, EFFORT), req.prompt, emit)
        except _Cancelled:
            pass
        except anthropic.APIError as e:
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"type": "error", "message": f"Model call failed: {getattr(e, 'message', e)}"},
            )
        except Exception as e:  # surfaced to the page instead of a broken stream
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "message": str(e)})
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()

    async def stream() -> AsyncIterator[bytes]:
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield (json.dumps(event) + "\n").encode()
                if await request.is_disconnected():
                    break
        finally:
            cancelled.set()
            _run_slots.release()

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
