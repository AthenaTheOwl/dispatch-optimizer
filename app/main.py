"""DispatchIQ — Constrained vehicle routing and dispatch optimization."""

from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.api.routes import router

app = FastAPI(
    title="DispatchIQ",
    description="Constrained vehicle routing and dispatch optimization",
    version="0.1.0",
)

app.include_router(router)

# Serve static files
static_dir = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def serve_dashboard():
    """Serve the main dashboard HTML."""
    return FileResponse(str(static_dir / "index.html"))
