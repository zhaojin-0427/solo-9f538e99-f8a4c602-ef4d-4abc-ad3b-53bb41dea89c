"""FastAPI instance #1: build the block-free handoff package."""
from fastapi import FastAPI

from .analysis import build_analysis_package
from .analysis_models import AnalysisBuildRequest, AnalysisPackage
from .engine import build_package
from .models import BuildRequest, HandoffPackage

app = FastAPI(
    title="District Re-zoning Package Builder",
    description=(
        "Submit old/new district codes together with the street-block IDs "
        "each district covers (a block may belong to several districts). "
        "Returns a JSON handoff package containing only district codes and "
        "the exact algebraic relations between their populations — no block "
        "IDs or per-block memberships are retained.\n\n"
        "`POST /analysis/build` builds the version-2 county-boundary "
        "analysis package: give the same old/new block lists plus one group "
        "of old codes (baseline boundary) and one group of new codes "
        "(report boundary). Blocks are deduplicated inside each group. "
        "The returned package can be reused offline."
    ),
    version="2.0.0",
)


@app.get("/", tags=["meta"])
def root():
    return {
        "service": "package-builder",
        "docs": "/docs",
        "endpoint": "/build",
        "analysis_endpoint": "/analysis/build",
    }


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


@app.post(
    "/analysis/build",
    response_model=AnalysisPackage,
    tags=["analysis"],
    summary="Build the county-boundary analysis package",
)
def build_analysis(request: AnalysisBuildRequest) -> AnalysisPackage:
    package = build_analysis_package(
        old_districts=[d.model_dump() for d in request.old_districts],
        new_districts=[d.model_dump() for d in request.new_districts],
        before_districts=list(request.before_districts),
        after_districts=list(request.after_districts),
    )
    return AnalysisPackage(**package)


# Convenience aliases for the analysis package entry point. They are hidden
# from the OpenAPI schema (the canonical path is /analysis/build) but all
# return exactly the same package.
for _alias in (
    "/analysis/package",
    "/analysis/build-package",
    "/build-analysis",
    "/build_analysis",
):
    app.add_api_route(
        _alias,
        build_analysis,
        methods=["POST"],
        response_model=AnalysisPackage,
        include_in_schema=False,
    )
