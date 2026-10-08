"""Run the mobile group-dining web app: python webapp.py [--demo] [--ollama]."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from dining.accounts.email import AccountEmailService
from dining.api import build_router
from dining.api.operations import operations_router
from dining.catalog.audit import public_catalog_status
from dining.catalog.models import Catalog, load_catalog
from dining.core.runtime_env import load_env_file
from dining.core.store import DiningStore
from dining.llm.inference import InferenceSettings
from dining.notifications.reminders import NotificationService
from dining.recommendation.agent import DiningAgent
from dining.retrieval.index import load_persistent_index

ROOT = Path(__file__).resolve().parent


def create_app(
    db_path=None,
    catalog_path=None,
    demo_mode=False,
    use_model=False,
    *,
    async_generation=True,
    generation_options=None,
    inference_settings: InferenceSettings | None = None,
    index_path=None,
    embedding_index=None,
):
    if catalog_path:
        catalog = load_catalog(catalog_path, allow_synthetic=demo_mode)
    elif demo_mode:
        catalog = load_catalog(ROOT / "data/catalog.example.json", allow_synthetic=True)
    else:
        catalog = Catalog(
            schema_version="2",
            catalog_id="awaiting-import",
            version="0",
            generated_at=datetime.now(timezone.utc),
            synthetic=False,
            brands=(),
            outlets=(),
            menu_items=(),
            sources=(),
        )

    if embedding_index is None and not demo_mode:
        idx_dir = index_path or (ROOT / "var/vector/catalog")
        if idx_dir.exists() and (idx_dir / "active_index.json").exists():
            try:
                embedding_index = load_persistent_index(idx_dir, catalog=catalog)
            except Exception:
                embedding_index = None

    store = DiningStore(db_path or ROOT / "var/dining.sqlite3")
    agent = DiningAgent(
        catalog,
        use_model=use_model,
        settings=inference_settings,
        embedding_index=embedding_index,
    )
    notifications = NotificationService(store)
    account_email = AccountEmailService(store)

    @asynccontextmanager
    async def lifespan(app):
        stop = asyncio.Event()

        async def reminders():
            while not stop.is_set():
                try:
                    await asyncio.to_thread(notifications.tick)
                    if account_email.settings.enabled:
                        await asyncio.to_thread(account_email.tick)
                except Exception:  # noqa: BLE001 - retry a failed background sweep without logging private records.
                    logging.getLogger(__name__).error(
                        "Inbox reminder sweep failed; it will retry"
                    )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=30)
                except TimeoutError:
                    pass

        async def recommendations():
            while not stop.is_set():
                try:
                    await asyncio.to_thread(core.generation_worker.tick)
                except Exception:  # noqa: BLE001 - never log private provider or snapshot values.
                    logging.getLogger(__name__).error(
                        "Recommendation worker sweep failed; it will retry"
                    )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1)
                except TimeoutError:
                    pass

        tasks = [asyncio.create_task(reminders())]
        if async_generation:
            tasks.append(asyncio.create_task(recommendations()))
        try:
            yield
        finally:
            stop.set()
            await asyncio.gather(*tasks)

    app = FastAPI(
        title="Makan Together",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.store, app.state.catalog, app.state.agent = store, catalog, agent
    app.state.notifications = notifications
    app.state.account_email = account_email
    allowed_hosts = os.getenv(
        "DINING_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],testserver"
    ).split(",")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            length = request.headers.get("content-length")
            try:
                if length is not None and int(length) > 65536:
                    return JSONResponse(
                        {"detail": "Request is too large"}, status_code=413
                    )
            except ValueError:
                return JSONResponse(
                    {"detail": "Invalid request length"}, status_code=400
                )
            if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
                data = bytearray()
                async for chunk in request.stream():
                    data.extend(chunk)
                    if len(data) > 65536:
                        return JSONResponse(
                            {"detail": "Request is too large"}, status_code=413
                        )
                request._body = bytes(data)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "geolocation=(self), microphone=(), camera=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    core = build_router(
        store,
        agent,
        demo_mode=catalog.synthetic,
        account_access=account_email.assert_verified,
        async_generation=async_generation,
        generation_options=generation_options,
    )
    app.state.generation_worker = core.generation_worker
    app.include_router(core)
    app.include_router(notifications.router(core.authenticate, core.room_member))
    app.include_router(account_email.router(core.authenticate))
    app.include_router(operations_router(store, core.generation_worker, agent.settings))

    @app.get("/operations")
    def operations():
        return FileResponse(
            ROOT / "web/operations.html", headers={"Cache-Control": "no-store"}
        )

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "catalog_version": catalog.version,
            "demo": catalog.synthetic,
        }

    @app.get("/api/catalog/status")
    def catalog_status():
        idx = getattr(agent, "embedding_index", None)
        if idx and idx.is_usable_for_catalog(catalog):
            retrieval_info = {
                "mode": "semantic",
                "index_available": True,
                "catalog_version": catalog.version,
                "embedding_model": getattr(idx, "model_name", "unknown"),
                "index_policy_version": getattr(idx, "index_policy_version", "unknown"),
                "indexed_item_count": getattr(idx, "item_count", 0),
                "indexed_outlet_count": getattr(idx, "outlet_count", 0),
            }
        else:
            retrieval_info = {"mode": "structured", "index_available": False}
        return {
            **public_catalog_status(catalog),
            "retrieval": retrieval_info,
        }

    @app.get("/api/inference/status")
    def inference_status():
        # Configuration is not evidence that a request reached the provider.
        return agent.settings.public()

    app.mount("/assets", StaticFiles(directory=ROOT / "web"), name="assets")

    @app.get("/")
    def index():
        return FileResponse(
            ROOT / "web/index.html", headers={"Cache-Control": "no-store"}
        )

    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(
            ROOT / "web/manifest.webmanifest", media_type="application/manifest+json"
        )

    @app.get("/favicon.svg")
    def icon():
        return FileResponse(ROOT / "web/favicon.svg", media_type="image/svg+xml")

    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Explicit server-side environment file; exported variables take precedence",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Explicitly allow clearly labelled fictional catalog data",
    )
    parser.add_argument(
        "--ollama",
        action="store_true",
        help="Use local Ollama for bounded explanation-label selection",
    )
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()
    try:
        env_file = args.env_file or (ROOT / "var/inference.env" if (ROOT / "var/inference.env").exists() else None)
        load_env_file(env_file)
    except ValueError as error:
        parser.error(str(error))
    uvicorn.run(
        create_app(args.db, args.catalog, args.demo, args.ollama),
        host="127.0.0.1",
        port=args.port,
    )
