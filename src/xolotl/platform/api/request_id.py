"""Request id propagation and one JSON log line per request."""

import logging
import re
import time
from typing import cast

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from xolotl.platform.ids import uuid7
from xolotl.platform.logging import request_id_var

logger = logging.getLogger("xolotl.request")
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")


class RequestIdMiddleware:
    """Honour a well-formed ``X-Request-Id`` or generate one; echo it back."""

    def __init__(self, app: ASGIApp) -> None:
        self.app: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if cast("str", scope["type"]) != "http":
            await self.app(scope, receive, send)
            return
        incoming = Headers(scope=scope).get("x-request-id", "")
        request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else uuid7().hex
        status = {"code": 0}
        started = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            if cast("str", message["type"]) == "http.response.start":
                status["code"] = cast("int", message["status"])
                MutableHeaders(scope=message)["X-Request-Id"] = request_id
            await send(message)

        # Starlette's ServerErrorMiddleware wraps this middleware, so the 500 it
        # builds never passes send_with_id; problem() reads the id back from here.
        state = cast("dict[str, object]", scope.setdefault("state", {}))
        state["request_id"] = request_id
        token = request_id_var.set(request_id)
        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            status["code"] = 500  # ServerErrorMiddleware will answer with a 500
            raise
        finally:
            request_id_var.reset(token)
            logger.info(
                "request",
                extra={
                    "method": cast("str", scope["method"]),
                    "path": cast("str", scope["path"]),
                    "status": status["code"],
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "request_id": request_id,
                },
            )
