import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send


_DOCUMENT_UPLOAD_PATH = re.compile(
    r"/api/v1/subjects/[^/]+/study-spaces/[^/]+/documents\Z"
)


class RequestBodyLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int,
        allowed_origins: set[str],
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not _DOCUMENT_UPLOAD_PATH.fullmatch(scope.get("path", ""))
        ):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = 0
            if declared_size > self.max_bytes:
                await self._reject(scope, send)
                return

        received_bytes = 0

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_bytes:
                    raise _RequestTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestTooLarge:
            await self._reject(scope, send)

    async def _reject(self, scope: Scope, send: Send) -> None:
        body = b'{"detail":"Upload request exceeds the maximum size"}'
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
        ]
        request_headers = dict(scope.get("headers", []))
        origin = request_headers.get(b"origin", b"").decode("latin-1")
        if origin in self.allowed_origins:
            headers.extend(
                [
                    (b"access-control-allow-origin", origin.encode("latin-1")),
                    (b"access-control-allow-credentials", b"true"),
                    (b"vary", b"Origin"),
                ]
            )
        await send({"type": "http.response.start", "status": 413, "headers": headers})
        await send(
            {
                "type": "http.response.body",
                "body": body,
            }
        )


class _RequestTooLarge(Exception):
    pass
