"""FastAPI instance #1: build the block-free handoff package."""
from fastapi import FastAPI

from .engine import build_package
from .models import BuildRequest, HandoffPackage

app = FastAPI(
    title="District Re-zoning Package Builder",
    description=(
        "Submit old/new district codes together with the street-block IDs "
        "each district covers (a block may belong to several districts). "
        "Returns a JSON handoff package containing only district codes and "
        "the exact algebraic relations between their populations — no block "
        "IDs or per-block memberships are retained."
    ),
    version="1.0.0",
)


@app.get("/", tags=["meta"])
def root():
    return {"service": "package-builder", "docs": "/docs", "endpoint": "/build"}


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


@app.post(
    "/build",
    response_model=HandoffPackage,
    tags=["build"],
    summary="Build the JSON handoff package",
)
def build(request: BuildRequest) -> HandoffPackage:
    package = build_package(
        old_districts=[d.model_dump() for d in request.old_districts],
        new_districts=[d.model_dump() for d in request.new_districts],
    )
    return HandoffPackage(**package)
