"""Entry point for Orbital Industries API (FastAPI + Uvicorn)."""
import os

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=os.getenv("UVICORN_RELOAD", "false").lower() == "true",
        workers=int(os.getenv("UVICORN_WORKERS", "1")),
        access_log=True,
        loop="asyncio",
    )
