from __future__ import annotations

from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles


class SpaStaticFiles(StaticFiles):
    """Serve built assets and fall back to index.html for client-side routes."""

    def __init__(self, directory: Path) -> None:
        super().__init__(directory=str(directory), html=True, check_dir=True)
        self.index_path = directory / "index.html"

    async def get_response(self, path: str, scope: dict) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or Path(path).suffix:
                raise
            return FileResponse(self.index_path)
        if response.status_code != 404 or Path(path).suffix:
            return response
        return FileResponse(self.index_path)
