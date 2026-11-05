from collections.abc import Awaitable, Callable

from fastapi import FastAPI
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.responses import Response

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.security.csrf import validate_request_origin

settings = get_settings()


def _frontend_origins() -> list[str]:
    from urllib.parse import urlparse

    origins = {settings.frontend_origin, settings.frontend_base_url}
    return sorted(
        {
            f"{parsed.scheme}://{parsed.netloc}"
            for value in origins
            if (parsed := urlparse(value)).scheme and parsed.netloc
        }
    )

app = FastAPI(
    title="Viacora API",
    description="The API foundation for your personal learning space.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_frontend_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
    expose_headers=["Retry-After"],
)


@app.middleware("http")
async def security_headers(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    safe_methods = {"GET", "HEAD", "OPTIONS"}
    if request.url.path.startswith("/api/v1") and request.method not in safe_methods:
        try:
            validate_request_origin(request)
        except HTTPException as error:
            response = JSONResponse(
                {"detail": error.detail},
                status_code=error.status_code,
                headers=error.headers,
            )
        else:
            response = await call_next(request)
    else:
        response = await call_next(request)
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith("/api/v1/auth"):
        response.headers["Cache-Control"] = "no-store"
    return response


app.include_router(api_router, prefix="/api/v1")
