from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api.router import router as api_router
from app.application.settings import SettingsService
from app.config import AppConfig
from app.errors import install_error_handlers
from app.media.registry import SourceMediaRegistry
from app.repositories.database import Database
from app.security.access import SESSION_COOKIE_NAME, AccessGate, LoginRateLimiter
from app.security.secrets import SecretCipher
from app.sources.base import ComicSource
from app.sources.manga18fx import Manga18fxSource
from app.web import SpaStaticFiles


def create_app(
    config: AppConfig | None = None, *, comic_source: ComicSource | None = None
) -> FastAPI:
    resolved_config = config or AppConfig.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_config.ensure_directories()
        app.state.config = resolved_config
        database = Database(resolved_config.database_path)
        cipher = SecretCipher(resolved_config.secrets_path, database)
        app.state.database = database
        app.state.secret_cipher = cipher
        app.state.settings_service = SettingsService(database, cipher, resolved_config)
        app.state.access_gate = AccessGate(
            resolved_config.access_password,
            cipher.derive_key("access-session"),
        )
        app.state.login_rate_limiter = LoginRateLimiter()
        source = comic_source or Manga18fxSource(
            base_url=resolved_config.upstream_base_url,
            timeout=resolved_config.request_timeout,
        )
        app.state.comic_source = source
        app.state.media_registry = SourceMediaRegistry()
        try:
            yield
        finally:
            if comic_source is None:
                await source.aclose()
            database.close()

    app = FastAPI(
        title="ComicLens",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.config = resolved_config
    install_error_handlers(app)

    @app.middleware("http")
    async def enforce_access_gate(request: Request, call_next):
        public_paths = {
            "/health",
            "/api/auth/config",
            "/api/auth/session",
            "/api/auth/login",
            "/api/auth/logout",
        }
        gate: AccessGate = request.app.state.access_gate
        if (
            gate.enabled
            and request.url.path.startswith("/api/")
            and request.url.path not in public_paths
            and not gate.validate_session(request.cookies.get(SESSION_COOKIE_NAME))
        ):
            return JSONResponse(
                status_code=401,
                content={
                    "code": "AUTH_REQUIRED",
                    "message": "需要访问密码",
                    "retryable": False,
                },
            )
        return await call_next(request)

    app.include_router(api_router)

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def unknown_api_route(path: str) -> None:
        del path
        raise HTTPException(status_code=404, detail="API 接口不存在")

    if resolved_config.static_dir.joinpath("index.html").is_file():
        app.mount("/", SpaStaticFiles(resolved_config.static_dir), name="web")

    return app


logging.basicConfig(level=AppConfig.from_env().log_level)
app = create_app()
