"""Test-image-only factories; reuse the actual API and native adapter routers."""
import os

from fastapi import Request
from fastapi.responses import JSONResponse

MODEL_INPUTS = ("OPENAI_API_KEY", "OPENAI_API_KEY_FILE", "CODEX_API_KEY_FILE", "CODEX_ACCESS_TOKEN", "ACCESS_TOKEN")


def require_offline():
    if os.environ.get("APP_ENV") != "test" or os.environ.get("OFFLINE_RUNNER_FIXTURE") != "true":
        raise RuntimeError("Offline services require an explicit test-only environment")
    if any(os.environ.get(name) for name in MODEL_INPUTS):
        raise RuntimeError("Model credentials must not be provided to offline fixtures")


def create_api():
    require_offline()
    from app.main import create_app
    app = create_app()
    app.state.codex_verification = "fixture"
    return app


def create_runner():
    require_offline()
    if os.environ.get("CODEX_BIN") != "/usr/local/bin/codex-fixture" or os.environ.get("CODEX_MODEL") != "fixture-no-provider":
        raise RuntimeError("Offline runner requires the test-only protocol executable")
    from runner.app import create_app
    app = create_app()
    owner = os.environ["RUNNER_ACCOUNT_ID"]

    @app.middleware("http")
    async def definite_rejection(request: Request, call_next):
        if request.method == "POST" and request.url.path.endswith("/turn"):
            # Test-only intake rejection, outside the production API/adapter.
            # Reuse the adapter's authentication check before emitting a result.
            import hmac
            from pathlib import Path
            expected = Path(os.environ["RUNNER_TOKEN_FILE"]).read_text().strip()
            if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + expected):
                return JSONResponse({"error": "unauthenticated"}, status_code=401)
            body = await request.json()
            if body.get("text") == "/fixture/reject":
                return JSONResponse({"ownerId": owner, "error": "conflict"}, status_code=409, headers={"Cache-Control": "no-store"})
        return await call_next(request)

    return app
