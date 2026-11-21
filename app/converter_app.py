"""FastAPI instance #2: convert old-district populations using a package."""
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .analysis import convert_analysis
from .analysis_models import (
    AnalysisConvertRequest,
    AnalysisConvertResponse,
    AnalysisErrorResponse,
)
from .engine import ConversionError, convert
from .models import ConvertRequest, ConvertResponse, ErrorResponse

app = FastAPI(
    title="District Re-zoning Population Converter",
    description=(
        "Submit a handoff package (from the builder instance) and the "
        "currently available old-district populations (non-negative "
        "integers). Returns the new-district populations that can be "
        "derived exactly, together with their fractional-coefficient "
        "formulas; every other new district is marked as missing data. "
        "A contradicted coverage relation, or a negative/non-integer "
        "result, makes the whole request fail with an error response.\n\n"
        "`POST /analysis/convert` takes the version-2 analysis package plus "
        "the baseline old-district and report new-district populations "
        "(both non-negative integers) and returns the three independent "
        "differences — total change, boundary effect and same-coverage "
        "change — each with its fractional-coefficient formula. "
        "A contradicted checkable coverage relation, or a computable "
        "difference that is not an integer, fails the whole request."
    ),
    version="2.0.0",
)


@app.exception_handler(ConversionError)
def _conversion_error_handler(_request, exc: ConversionError):
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(
            error="conversion_failed", detail=str(exc)
        ).model_dump(),
    )


@app.get("/", tags=["meta"])
def root():
    return {
        "service": "population-converter",
        "docs": "/docs",
        "endpoint": "/convert",
        "analysis_endpoint": "/analysis/convert",
    }


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


@app.post(
    "/convert",
    response_model=ConvertResponse,
    responses={400: {"model": ErrorResponse}},
    tags=["convert"],
    summary="Convert known old populations into new-district populations",
)
def run_convert(request: ConvertRequest) -> ConvertResponse:
    result = convert(
        package=request.package.model_dump(),
        old_populations=dict(request.old_populations),
    )
    return ConvertResponse(**result)


@app.post(
    "/analysis/convert",
    response_model=AnalysisConvertResponse,
    responses={400: {"model": AnalysisErrorResponse}},
    tags=["analysis"],
    summary="Compute total change, boundary effect and same-coverage change",
)
def run_analysis_convert(
    request: AnalysisConvertRequest,
) -> AnalysisConvertResponse:
    result = convert_analysis(
        package=request.package.model_dump(),
        old_populations=dict(request.old_populations),
        new_populations=dict(request.new_populations),
    )
    return AnalysisConvertResponse(**result)


# Convenience aliases; the canonical path shown in the docs is
# /analysis/convert.
for _alias in (
    "/analysis/convert-package",
    "/analysis/compute",
    "/convert-analysis",
    "/convert_analysis",
):
    app.add_api_route(
        _alias,
        run_analysis_convert,
        methods=["POST"],
        response_model=AnalysisConvertResponse,
        responses={400: {"model": AnalysisErrorResponse}},
        include_in_schema=False,
    )
