from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router

app = FastAPI(title="Medical Concierge")

# The same router is served at both paths. `/api` is what the bundled web UI
# has always used and keeps working unchanged; `/api/v1` is the versioned
# contract a native client should target, so that a future breaking change can
# ship as `/api/v2` without stranding installed apps. See docs/APP_ROADMAP.md.
app.include_router(router, prefix="/api")
app.include_router(router, prefix="/api/v1")

_static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
