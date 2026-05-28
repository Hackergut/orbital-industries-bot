"""FastAPI entry point for Orbital Industries API."""
import asyncio
import threading
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import Config
from app.browser_selenium import shutdown_pool

app = FastAPI(title="Orbital Industries API", version="2.0.0")

app.add_middleware(
    SessionMiddleware,
    secret_key=Config.SECRET_KEY,
    max_age=3600,
    same_site="lax",
    https_only=False,
)

app.mount("/static", StaticFiles(directory="static"), name="static")


def _run_pipeline_in_thread():
    """Run pipeline in a dedicated thread with its own event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        from app.pipeline_async import run_pipeline_async
        loop.run_until_complete(run_pipeline_async())
    except Exception as e:
        print(f"[PIPELINE-THREAD] Error: {e}")
    finally:
        loop.close()


@app.on_event("startup")
async def startup_event():
    """Auto-start the infinite pipeline loop in a background thread."""
    try:
        t = threading.Thread(target=_run_pipeline_in_thread, daemon=True)
        t.start()
        print("[BOOT] Pipeline started in background thread — runs until container stops")
    except Exception as e:
        print(f"[BOOT] Failed to start pipeline thread: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    await shutdown_pool()


# Import routes after app creation to avoid circular imports
from app.routes_fastapi import router
from app.live import router as live_router
from app.history import router as history_router
from app.temporal_api import router as temporal_router
app.include_router(router)
app.include_router(live_router)
app.include_router(history_router)
from app.live_simple import router as live_simple_router
app.include_router(live_simple_router)
app.include_router(temporal_router)
