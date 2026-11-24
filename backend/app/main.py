import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import auth, cases, health, intake, stages, workspace
from app.core.config import settings
from app.core.database import Base, engine
from app.services.accounts import initialize_accounts
from app.services.intake_store import process_next_job


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.app_env == "dev" and engine.dialect.name == "sqlite":
        Base.metadata.create_all(engine)
    if settings.app_env != "dev":
        yield
        return
    initialize_accounts()

    stopped = asyncio.Event()
    worker = asyncio.create_task(run_intake_worker(stopped))
    try:
        yield
    finally:
        stopped.set()
        await worker


async def run_intake_worker(stopped: asyncio.Event) -> None:
    logger = logging.getLogger(__name__)
    while not stopped.is_set():
        try:
            if await asyncio.to_thread(process_next_job):
                continue
        except Exception as exc:
            logger.error("Intake worker could not process a job (%s).", type(exc).__name__)
        try:
            await asyncio.wait_for(stopped.wait(), timeout=1)
        except TimeoutError:
            continue


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="MDE-NG workspace API with a local development workflow.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "If-Match"],
)

app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(workspace.router, prefix="/api")
app.include_router(cases.router, prefix="/api")
app.include_router(intake.router, prefix="/api")
app.include_router(stages.router, prefix="/api")


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request, exc):
    if request.url.path.rstrip("/") in {"/api/auth/login", "/api/auth/signup"}:
        return JSONResponse({"detail": "Check your email and password and try again."}, status_code=422)
    return await request_validation_exception_handler(request, exc)


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"service": settings.app_name, "docs": "/docs", "mode": settings.app_env}


@app.middleware("http")
async def disable_api_cache(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response
