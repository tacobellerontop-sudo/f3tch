"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .activity import ActivityService
from .api import admin as admin_api
from .api import downloads as downloads_api
from .api import files as files_api
from .api import network as network_api
from .api import profile as profile_api
from .api import providers as providers_api
from .api import ws as ws_api
from .api.deps import ApiError, is_local_request
from .config import BootConfig, load_config
from .context import AppContext
from .db import Database, run_migrations
from .engine.aria2 import Aria2Service, find_aria2
from .engine.guardproxy import GuardProxy
from .engine.http import HttpClient
from .engine.netpaths import NetworkPaths
from .engine.manager import DownloadManager
from .events import EventBus
from .maintenance import run_maintenance
from .providers.base import ProviderAdapter
from .providers.registry import ProviderRegistry
from .security.crypto import SecretBox
from .security.urlguard import UrlGuard
from .settings_service import SettingsService
from .updates import UpdateService
from .storage import StorageManager

log = logging.getLogger("fetch.app")

MAINTENANCE_INTERVAL = 15 * 60
CSP = (
    "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; connect-src 'self' ws: wss:; font-src 'self' data:; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


class LocalOnlyMiddleware:
    """FETCH has no accounts, so it only talks to this computer.

    It listens on 127.0.0.1 only; this also refuses requests that were relayed
    here or that address FETCH by a name that is not this computer's. The
    second case is how a web page elsewhere would try to reach a local program
    (DNS rebinding).
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket") or is_local_request(scope):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = JSONResponse(
            {"error": {"code": "local_only", "message": "FETCH only answers to this computer."}},
            status_code=403,
        )
        await response(scope, receive, send)


def build_context(boot: BootConfig, providers: list[ProviderAdapter] | None = None) -> AppContext:
    boot.ensure_dirs()
    if not boot.secret_key:
        raise RuntimeError(
            "No secret key configured. Run 'python -m fetch_app init' (install.ps1 does "
            "this) to generate config/fetch.toml."
        )
    run_migrations(boot.database_url)
    db = Database(boot.database_url)
    settings = SettingsService(db, boot)
    bus = EventBus()
    activity = ActivityService(db, bus)
    storage = StorageManager(Path(settings.effective_storage_root()))
    storage.ensure()
    guard = UrlGuard(boot.ssrf_allowlist)
    http = HttpClient(guard)
    netpaths = NetworkPaths(guard, bus, settings.get)
    http.on_bytes = netpaths.default_meter.add
    box = SecretBox(boot.secret_key)
    registry = ProviderRegistry(db, http, box, providers)
    registry.sync()
    proxy = GuardProxy(guard)
    registry.proxy = proxy
    downloads_settings = settings.get().downloads
    executable = find_aria2(downloads_settings.aria2_path, boot.home) if downloads_settings.use_aria2 else None
    aria2 = Aria2Service(executable, boot.data_dir, proxy)
    if not downloads_settings.use_aria2:
        aria2.error = ""
    manager = DownloadManager(
        db=db, settings=settings, storage=storage, registry=registry, http=http,
        bus=bus, activity=activity, aria2=aria2, netpaths=netpaths,
    )
    return AppContext(
        boot=boot, db=db, settings=settings, bus=bus, activity=activity, storage=storage,
        guard=guard, http=http, box=box, registry=registry, proxy=proxy, aria2=aria2,
        manager=manager, updates=UpdateService(boot, http), netpaths=netpaths,
        control_token=secrets.token_urlsafe(32),
    )


async def _maintenance_loop(ctx: AppContext) -> None:
    while True:
        try:
            await asyncio.to_thread(run_maintenance, ctx)
        except Exception:
            log.exception("Maintenance pass failed")
        await asyncio.sleep(MAINTENANCE_INTERVAL)


def create_app(boot: BootConfig | None = None, providers: list[ProviderAdapter] | None = None) -> FastAPI:
    boot = boot or load_config()
    ctx = build_context(boot, providers)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ctx.bus.bind_loop(asyncio.get_running_loop())
        await ctx.http.start()
        await ctx.proxy.start()
        if ctx.aria2.executable:
            await ctx.aria2.start()
        await ctx.netpaths.start()
        await ctx.manager.start()
        housekeeping = asyncio.create_task(_maintenance_loop(ctx), name="fetch-maintenance")
        log.info("FETCH started", extra={"version": __version__, "storage_root": str(ctx.storage.root)})
        await asyncio.to_thread(ctx.activity.record, "system", "startup", f"FETCH {__version__} started")
        try:
            yield
        finally:
            housekeeping.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await housekeeping
            await ctx.manager.stop()
            await ctx.netpaths.stop()
            await ctx.aria2.stop()
            await ctx.proxy.stop()
            await ctx.http.close()
            await asyncio.to_thread(ctx.activity.record, "system", "shutdown", "FETCH stopped")
            log.info("FETCH stopped")
            ctx.db.dispose()

    app = FastAPI(
        title="FETCH API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.ctx = ctx

    app.add_middleware(LocalOnlyMiddleware)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "same-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        path = request.url.path
        if path.startswith("/api/"):
            headers.setdefault("Cache-Control", "no-store")
            if path.startswith("/api/docs"):
                return response
        headers.setdefault("Content-Security-Policy", CSP)
        return response

    def error(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError):
        return error(exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", ()) if p not in ("body", "query", "path"))
        message = str(first.get("msg", "Invalid request.")).removeprefix("Value error, ")
        return error(422, "validation_error", f"{field}: {message}" if field else message)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        # Details go to the log; the client only learns that something broke.
        log.error("Unhandled error on %s %s", request.method, request.url.path,
                  exc_info=(type(exc), exc, exc.__traceback__))
        return error(500, "internal_error", "Something went wrong on the server.")

    for module in (profile_api, downloads_api, files_api, providers_api, admin_api, network_api, ws_api):
        app.include_router(module.router, prefix="/api")

    @app.get("/api/health", include_in_schema=False)
    def liveness() -> dict:
        return {"status": "ok", "version": __version__}

    dist = boot.frontend_dist

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   include_in_schema=False)
    def api_not_found(rest: str):
        return error(404, "not_found", "Unknown API endpoint.")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        index = dist / "index.html"
        if not index.is_file():
            return PlainTextResponse(
                "FETCH is running, but the web interface has not been built yet.\n"
                "Run install.ps1 (or 'npm install && npm run build' in the frontend folder).",
                status_code=503,
            )
        if path:
            candidate = (dist / path).resolve()
            if candidate.is_file() and candidate.is_relative_to(dist.resolve()):
                cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
                return FileResponse(candidate, headers={"Cache-Control": cache})
            if path.startswith("assets/"):
                return error(404, "not_found", "Not found.")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
