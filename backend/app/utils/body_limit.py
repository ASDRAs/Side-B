"""Reject oversized request bodies before FastAPI parses them.

FastAPI reads and decodes a JSON body before running dependencies, so the
authentication check cannot bound the input size. The approval endpoints take
tiny bodies; anything larger is refused with 413 without being buffered.
"""

import json


class _BodyTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    def __init__(self, app, *, path_prefixes: tuple[str, ...], max_bytes: int) -> None:
        self.app = app
        self._prefixes = path_prefixes
        self._max_bytes = max(0, max_bytes)

    async def _reject(self, send) -> None:
        body = json.dumps(
            {
                "detail": {
                    "code": "request_too_large",
                    "message": "요청 본문이 너무 큽니다.",
                }
            }
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not str(scope.get("path", "")).startswith(
            self._prefixes
        ):
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = self._max_bytes + 1
                if declared > self._max_bytes:
                    await self._reject(send)
                    return

        received = 0
        started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_bytes:
                    raise _BodyTooLarge()
            return message

        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _BodyTooLarge:
            if started:
                raise
            await self._reject(send)
