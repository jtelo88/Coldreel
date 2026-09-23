"""API HTTP (FastAPI) y front estático.

Rutas:
  GET  /                              → interfaz
  GET  /api/health                    → estado del índice y la caché
  GET  /api/cameras                   → cámaras (por MAC) con rango y tamaño
  GET  /api/coverage?mac&tz           → días con vídeo de una cámara, en la zona horaria dada
  GET  /api/timeline?mac&from_ms&to_ms[&channel] → ficheros de la ventana con sus particiones
  POST /api/index?mac&from_ms&to_ms   → indexa (ubv-info) los ficheros sin particiones de la ventana
  POST /api/sync                      → relee el registro de nvr-backup
  GET  /api/files/{id}                → un fichero con sus particiones y estado de caché
  GET  /clip/{file_id}/{ordinal}.mp4  → MP4 de una partición (remux bajo demanda, con Range)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, service
from .config import Settings
from .index import Index
from .remux import RemuxCache, RemuxError, UnsupportedCodec

log = logging.getLogger("coldreel")
WEB_DIR = Path(__file__).parent / "web"


def create_app(settings: Settings, index: Index | None = None, cache: RemuxCache | None = None) -> FastAPI:
    index = index or Index(settings.db_path)
    cache = cache or RemuxCache(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Primera sincronización ANTES de servir: así la interfaz nunca ve un índice a medias.
        try:
            await asyncio.to_thread(service.sync, index, settings)
        except Exception as e:  # noqa: BLE001
            log.error("sync inicial: %s", e)

        async def periodic():
            while settings.sync_interval_s > 0:
                await asyncio.sleep(settings.sync_interval_s)
                try:
                    await asyncio.to_thread(service.sync, index, settings)
                except Exception as e:  # noqa: BLE001
                    log.error("sync periódico: %s", e)

        task = asyncio.create_task(periodic())
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(title="Coldreel", version=__version__, lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
    app.state.settings, app.state.index, app.state.cache = settings, index, cache

    if settings.auth:
        user, _, password = settings.auth.partition(":")

        @app.middleware("http")
        async def basic_auth(request: Request, call_next):
            header = request.headers.get("authorization", "")
            ok = False
            if header.startswith("Basic "):
                try:
                    u, _, p = base64.b64decode(header[6:]).decode().partition(":")
                    ok = secrets.compare_digest(u, user) and secrets.compare_digest(p, password)
                except Exception:  # noqa: BLE001
                    ok = False
            if not ok:
                return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Coldreel"'})
            return await call_next(request)

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def root():
        return FileResponse(WEB_DIR / "index.html", media_type="text/html")

    @app.get("/api/health")
    def health():
        total, _ = cache.usage()
        return {"ok": True, "version": __version__, "index": index.stats(),
                "cache": {"bytes": total, "max_bytes": settings.cache_max_bytes}}

    @app.get("/api/cameras")
    def cameras():
        return index.cameras()

    @app.get("/api/coverage")
    def coverage(mac: str, tz: str = "UTC", channel: int = 0):
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(400, f"zona horaria desconocida: {tz}")
        return index.coverage(mac, tz, channel)

    def _window(from_ms: int, to_ms: int):
        if to_ms <= from_ms:
            raise HTTPException(400, "to_ms debe ser mayor que from_ms")
        if to_ms - from_ms > 31 * 86400_000:
            raise HTTPException(400, "ventana máxima: 31 días")

    @app.get("/api/timeline")
    def timeline(mac: str, from_ms: int, to_ms: int, channel: int = 0):
        _window(from_ms, to_ms)
        files = index.timeline(mac, from_ms, to_ms, channel)
        for f in files:
            man = cache.manifest(f["id"])
            for p in f["partitions"]:
                p["clip_url"] = f"/clip/{f['id']}/{p['ordinal']}.mp4"
                p["cached"] = bool(man and str(p["ordinal"]) in man.get("clips", {}))
            f["cached"] = man is not None
        return {"mac": mac, "from_ms": from_ms, "to_ms": to_ms, "files": files}

    @app.post("/api/index")
    def index_window(mac: str, from_ms: int, to_ms: int, channel: int = 0, limit: int = Query(200, le=1000)):
        _window(from_ms, to_ms)
        rows = service.unindexed_in_window(index, mac, from_ms, to_ms, channel, limit)
        if not rows:
            return {"indexed": 0, "errors": [], "partitions": 0}
        return service.index_files(index, settings, rows)

    @app.post("/api/sync")
    def sync():
        return service.sync(index, settings)

    @app.get("/api/files/{file_id}")
    def file_detail(file_id: int):
        f = index.file(file_id)
        if not f:
            raise HTTPException(404, "fichero no encontrado")
        f["partitions"] = index.partitions(file_id)
        f["manifest"] = cache.manifest(file_id)
        return f

    @app.api_route("/clip/{file_id}/{ordinal}.mp4", methods=["GET", "HEAD"])
    def clip(file_id: int, ordinal: int):
        """HEAD sirve para disparar el remux y esperar sin descargar; GET admite `Range` (seek)."""
        f = index.file(file_id)
        if not f:
            raise HTTPException(404, "fichero no encontrado")
        path = cache.clip_path(file_id, ordinal)
        if path is None:
            if f["indexed_at"] is None:
                service.index_file(index, settings, f)
                f = index.file(file_id)
            parts = index.partitions(file_id)
            if not any(p["ordinal"] == ordinal for p in parts):
                raise HTTPException(404, "partición no encontrada")
            try:
                cache.ensure(f, parts)
            except UnsupportedCodec as e:
                raise HTTPException(501, str(e))
            except RemuxError as e:
                raise HTTPException(500, str(e))
            path = cache.clip_path(file_id, ordinal)
            if path is None:
                raise HTTPException(500, "remux terminó pero no produjo el MP4 de esa partición")
        return FileResponse(path, media_type="video/mp4", filename=path.name)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("error no controlado en %s", request.url.path)
        return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)

    return app
