"""FastAPI entry point for Orbital Industries API."""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import Config
from app.browser_async import shutdown_pool

app = FastAPI(title="Orbital Industries API", version="2.0.0")

app.add_middleware(
    SessionMiddleware,
    secret_key=Config.SECRET_KEY,
    max_age=3600,
    same_site="lax",
    https_only=False,
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
async def startup_event():
    # Browser pool initializes lazily on first use to avoid blocking startup
    pass


@app.on_event("shutdown")
async def shutdown_event():
    await shutdown_pool()


# Import routes after app creation to avoid circular imports
from app.routes_fastapi import router
from app.live import router as live_router
from app.history import router as history_router
app.include_router(router)
app.include_router(live_router)
app.include_router(history_router)
