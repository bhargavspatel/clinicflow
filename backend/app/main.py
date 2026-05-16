import sentry_sdk
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from app.api.health import router as health_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.middleware import RequestLoggingMiddleware

configure_logging()
logger = structlog.get_logger(__name__)

settings = get_settings()

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
        ],
        traces_sample_rate=0.2,
        send_default_pii=False,
    )

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
)

# RequestLoggingMiddleware must be added before CORSMiddleware so that
# request_id is bound before any other middleware runs.
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "unhandled_exception",
        method=request.method,
        path=request.url.path,
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


from app.api.v1 import router as v1_router
app.include_router(health_router)
app.include_router(v1_router, prefix="/api/v1")
