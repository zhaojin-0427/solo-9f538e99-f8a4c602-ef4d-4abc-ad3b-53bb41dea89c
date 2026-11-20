"""FastAPI instance #2: convert old-district populations using a package."""
from fastapi import FastAPI
from fastapi.responses import JSONResponse

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
        "result, makes the whole request fail with an error response."
    ),
    version="1.0.0",
)


@app.exception_handler(ConversionError)
def _conversion_error_handler(_request, exc: ConversionError):
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(error="conversion_failed", detail=str(exc)).model_dump(),
    )


@app.get("/", tags=["meta"])
def root():
    return {"service": "population-converter", "docs": "/docs", "endpoint": "/convert"}


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
