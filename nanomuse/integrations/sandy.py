"""Host-controlled native computer service using the actual nanoMuse ComputerLink.

Run separately from the host. No MuseAgent, goals, memory, model, scheduler or
Sentinel approval queue is started. The authenticated host owns approvals.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from nanomuse.computer.link import ComputerLink
from nanomuse.config import HandsSettings


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    coordinate: tuple[int, int] | None = None
    text: str | None = Field(default=None, max_length=1000)
    scroll_direction: str | None = None
    scroll_amount: int = Field(default=3, ge=1, le=10)


def native_command(action: Action) -> dict[str, Any]:
    aliases = {
        "left_click": "click",
        "double_click": "double_click",
        "right_click": "right_click",
        "mouse_move": "move",
    }
    if action.action in aliases and action.coordinate is not None:
        x, y = action.coordinate
        if x < 0 or y < 0:
            raise HTTPException(422, "Coordinates must be nonnegative")
        return {"action": aliases[action.action], "x": x, "y": y}
    if action.action == "type" and action.text:
        # ComputerLink delegates Unicode typing to the native operator/clipboard.
        return {"action": "type", "text": action.text, "submit": False}
    if action.action == "key" and action.text:
        return {"action": "key", "keys": action.text.replace("Return", "enter").split("+")}
    if action.action == "scroll" and action.scroll_direction in ("up", "down"):
        x, y = action.coordinate or (0, 0)
        return {
            "action": "scroll",
            "x": x,
            "y": y,
            "dy": action.scroll_amount * (100 if action.scroll_direction == "down" else -100),
        }
    raise HTTPException(422, "Unsupported native action")


def create_app(*, link: ComputerLink | None = None, token: str | None = None) -> FastAPI:
    secret = token or os.environ.get("SANDY_NATIVE_TOKEN", "")
    if len(secret) < 32:
        raise RuntimeError("SANDY_NATIVE_TOKEN must contain at least 32 characters")
    temporary = tempfile.TemporaryDirectory(prefix="sandy-native-")

    # Construct the real link at startup only. Importing this module never takes
    # a screenshot, asks for OS permissions, or moves the user's pointer.
    @asynccontextmanager
    async def lifespan(app):
        nonlocal link
        link = link or ComputerLink(HandsSettings(enabled=True), shots_dir=Path(temporary.name))
        try:
            yield
        finally:
            link.stop()
            temporary.cleanup()

    app = FastAPI(lifespan=lifespan)
    lock = asyncio.Lock()
    latest: tuple[str, bytes, str] | None = None
    receipts: dict[str, tuple[str, dict]] = {}

    def auth(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, "Bearer " + secret):
            raise HTTPException(401, "Host authentication required")

    @app.post("/api/computer/desktop/actions", dependencies=[Depends(auth)])
    async def act(command: Action, idempotency_key: str = Header(default="")):
        nonlocal latest
        if command.action != "screenshot" and not idempotency_key:
            raise HTTPException(422, "Approved input requires an operation ID")
        digest = hashlib.sha256(
            json.dumps(command.model_dump(), sort_keys=True).encode()
        ).hexdigest()
        async with lock:
            if command.action != "screenshot" and idempotency_key in receipts:
                prior_digest, result = receipts[idempotency_key]
                if prior_digest != digest:
                    raise HTTPException(409, "Operation ID belongs to another action")
                if result["receipt"]["status"] == "interrupted":
                    raise HTTPException(409, "Input outcome is uncertain; reconcile with the host")
                return result
            receipt_id = str(uuid.uuid4())
            result = {"receipt": {"id": receipt_id, "status": "interrupted"}}
            if command.action != "screenshot":
                # Record uncertainty before handing control to OS input APIs.
                receipts[idempotency_key] = digest, result
                outcome = await link.act(native_command(command))
                screen = link.last_screen if outcome.get("looked") else None
            else:
                screen = await link.screen()
            if screen is None or screen.image_path is None:
                raise HTTPException(503, "No screen evidence; inspect before retrying")
            data = Path(screen.image_path).read_bytes()
            if len(data) > 5_000_000:
                raise HTTPException(503, "Screen exceeds the image limit")
            mime = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
            latest = receipt_id, data, mime
            result = {
                "receipt": {"id": receipt_id, "status": "succeeded"},
                "width": screen.width,
                "height": screen.height,
            }
            if command.action != "screenshot":
                receipts[idempotency_key] = digest, result
            # Observations stay in memory; do not retain a screenshot history.
            for path in Path(temporary.name).iterdir():
                if path.is_file():
                    path.unlink()
            return result

    @app.get("/api/computer/desktop/screenshot", dependencies=[Depends(auth)])
    async def screenshot(receipt: str):
        async with lock:
            if latest is None or latest[0] != receipt:
                raise HTTPException(404, "Screenshot superseded")
            return Response(latest[1], media_type=latest[2], headers={"Cache-Control": "no-store"})

    return app
