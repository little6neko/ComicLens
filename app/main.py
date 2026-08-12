from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from app import __version__
from app.api.router import router as api_router
from app.config import AppConfig
from app.errors import install_error_handlers
from app.media.registry import SourceMediaRegistry
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

    app = FastAPI(
        title="ComicLens",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.config = resolved_config
    install_error_handlers(app)
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
