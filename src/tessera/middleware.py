"""Custom ASGI middleware."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _PayloadTooLargeError(Exception):
    """Internal signal for oversized payloads."""


class RequestSizeLimitMiddleware:
    """Reject requests whose body exceeds a configurable size limit.

    Returns 413 Payload Too Large if the Content-Length header or
    streamed body exceeds *max_body_size* bytes.
    """

    def __init__(
        self, app: ASGIApp, max_body_size: int = 1_048_576
    ) -> None:
        self._app = app
        self._max_body_size = max_body_size

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """ASGI entry point."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # Check Content-Length header early
        headers = dict(scope.get("headers", []))
        content_length_raw = headers.get(b"content-length")
        if content_length_raw is not None:
            try:
                if int(content_length_raw) > self._max_body_size:
                    await self._send_413(send)
                    return
            except ValueError:
                pass

        # Wrap receive to count streamed bytes
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                received += len(body)
                if received > self._max_body_size:
                    raise _PayloadTooLargeError
            return message

        try:
            await self._app(scope, limited_receive, send)
        except _PayloadTooLargeError:
            await self._send_413(send)

    @staticmethod
    async def _send_413(send: Send) -> None:
        body = b'{"detail":"Payload too large"}'
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body)).encode()],
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })
