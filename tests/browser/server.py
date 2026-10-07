"""Disposable actual-app browser server. Never opens an existing pilot database."""

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from test_recommendation import ready_catalog

from scripts.seed_browser_e2e import seed
from webapp import create_app


def main():
    # A browser run must never send live mail or export private provider traces.
    for name in list(os.environ):
        if name.startswith(("DINING_SMTP_", "LANGSMITH_", "LANGCHAIN_")):
            os.environ.pop(name)
    os.environ["DINING_REQUIRE_VERIFIED_EMAIL"] = "false"
    os.environ["DINING_LLM_PROVIDER"] = "disabled"
    port = int(os.getenv("DINING_E2E_PORT", "7879"))
    with tempfile.TemporaryDirectory(prefix="makan-browser-") as folder:
        path = Path(folder)
        catalog = path / "catalog.json"
        catalog.write_text(ready_catalog().model_dump_json(), encoding="utf-8")
        app = create_app(path / "browser.sqlite3", catalog, demo_mode=True)
        seeded = threading.Event()

        @app.get("/e2e-ready")
        def ready():
            from fastapi.responses import JSONResponse

            return JSONResponse(
                {"ready": seeded.is_set()}, status_code=200 if seeded.is_set() else 503
            )

        def seed_after_start():
            base = f"http://127.0.0.1:{port}"
            for _ in range(100):
                try:
                    if httpx.get(base + "/health", timeout=1).status_code == 200:
                        for namespace in (
                            "chromium-desktop",
                            "chromium-narrow",
                            "webkit-mobile",
                        ):
                            seed(base, namespace=namespace)
                        seeded.set()
                        return
                except httpx.ConnectError:
                    time.sleep(0.1)
            raise RuntimeError("Disposable browser server did not become ready")

        thread = threading.Thread(target=seed_after_start, daemon=True)
        thread.start()
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
        thread.join(timeout=2)
        app.state.store.close()


if __name__ == "__main__":
    main()
