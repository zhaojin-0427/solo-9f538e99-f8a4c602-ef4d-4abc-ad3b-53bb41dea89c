"""Pydantic models for the county-boundary analysis endpoints."""
from typing import Any, Dict, List, Optional

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .models import DistrictInput


def _normalise_group(value: Any) -> Any:
    """Accept a group of district codes in any of the common shapes.

    Allowed: ``["O1", "O2"]``, ``[{"code": "O1"}, ...]``,
    ``{"districts": [...]}``, ``{"codes": [...]}``.
    """
    if isinstance(value, dict):
        for key in ("districts", "codes", "district_codes"):
            if key in value:
                return _normalise_group(value[key])
        raise ValueError(
            "boundary group must be a list of district codes or an object "
            "with a 'districts'/'codes' list"
        )
    if isinstance(value, (list, tuple)):
        codes: List[str] = []
        for item in value:
            if isinstance(item, str):
                codes.append(item)
            elif isinstance(item, dict) and "code" in item:
                codes.append(item["code"])
            else:
                raise ValueError(
                    "boundary group entries must be district codes or "
                    "objects with a 'code' field"
                )
        return codes
    raise ValueError("boundary group must be a list of district codes")


# --------------------------------------------------------------------------
# Builder request (/analysis/build)
# --------------------------------------------------------------------------
class AnalysisBuildRequest(BaseModel):
    """Old/new district block lists plus one group of district codes per side.

    ``before_districts`` are **old** district codes: their blocks, unioned,
    form the baseline old county boundary.  ``after_districts`` are **new**
    district codes: their blocks, unioned, form the report new county
    boundary.
    """

    model_config = ConfigDict(populate_by_name=True)

    old_districts: List[DistrictInput] = Field(
        ..., description="Old districts with the street blocks each covers."
    )
    new_districts: List[DistrictInput] = Field(
        ..., description="New districts with the street blocks each covers."
    )
    before_districts: List[str] = Field(
        ...,
        validation_alias=AliasChoices(
            "before_districts",
            "before_codes",
            "before_group",
            "before_old_districts",
            "old_boundary_districts",
            "old_boundary_codes",
            "baseline_districts",
            "baseline_codes",
            "pre_districts",
            "before",
            "before_group_codes",
            "old_group",
            "old_codes",
            "old_boundary",
            "old_boundary_district_codes",
        ),
        description="Old district codes forming the baseline (before) "
        "county boundary. Their blocks are deduplicated.",
    )
    after_districts: List[str] = Field(
        ...,
        validation_alias=AliasChoices(
            "after_districts",
            "after_codes",
            "after_group",
            "after_new_districts",
            "new_boundary_districts",
            "new_boundary_codes",
            "report_districts",
            "report_codes",
            "post_districts",
            "after",
            "after_group_codes",
            "new_group",
            "new_codes",
            "new_boundary",
            "new_boundary_district_codes",
        ),
        description="New district codes forming the report (after) county "
        "boundary. Their blocks are deduplicated.",
    )

    @field_validator("before_districts", "after_districts", mode="before")
    @classmethod
    def _normalise_boundary_groups(cls, value):
        return _normalise_group(value)

    @model_validator(mode="after")
    def _validate(self):
        for label, districts in (
            ("old_districts", self.old_districts),
            ("new_districts", self.new_districts),
        ):
            if not districts:
                raise ValueError(f"{label} must not be empty")
            codes = [d.code for d in districts]
            if len(set(codes)) != len(codes):
                raise ValueError(f"duplicate district code in {label}")
            for district in districts:
                if len(district.blocks) != len(set(district.blocks)):
                    raise ValueError(
                        f"duplicate block id in district {district.code}"
                    )

        old_codes = {d.code for d in self.old_districts}
        new_codes = {d.code for d in self.new_districts}
        # The before group names old districts and the after group names new
        # districts; a swapped or unknown code is a request error.
        unknown_before = set(self.before_districts) - old_codes
        if unknown_before:
            raise ValueError(
                "before_districts must be old district codes; unknown: "
                f"{sorted(unknown_before)}"
            )
        unknown_after = set(self.after_districts) - new_codes
        if unknown_after:
            raise ValueError(
                "after_districts must be new district codes; unknown: "
                f"{sorted(unknown_after)}"
            )
        return self


# --------------------------------------------------------------------------
# Analysis package (schema version 2)
# --------------------------------------------------------------------------
class BoundaryEntry(BaseModel):
    districts: List[str] = Field(
        default_factory=list,
        description="District codes whose blocks form this boundary.",
    )
    derivable: bool = Field(
        ..., description="Whether the boundary population is exactly "
        "expressible from the stored relations."
    )
    coefficients: Dict[str, str] = Field(
        default_factory=dict,
        description="Exact fraction coefficients keyed by district code "
        '(e.g. "2/3"); whole numbers look like "2".',
    )


class BoundaryEffectEntry(BaseModel):
    derivable: bool = Field(
        ..., description="Whether baseline_new_boundary - "
        "baseline_old_boundary is exactly expressible on its own."
    )
    coefficients: Dict[str, str] = Field(default_factory=dict)


class AnalysisPackage(BaseModel):
    schema_version: int = Field(2, description="Analysis package schema version.")
    kind: str = Field("boundary_analysis")
    old_districts: List[str]
    new_districts: List[str]
    old_relations: List[Dict[str, int]] = Field(
        default_factory=list,
        description="Exact equations between baseline old-district "
        "populations (weighted sum = 0).",
    )
    new_relations: List[Dict[str, int]] = Field(
        default_factory=list,
        description="Exact equations between report new-district "
        "populations (weighted sum = 0).",
    )
    old_boundary: BoundaryEntry
    new_boundary_baseline: BoundaryEntry
    new_boundary_report: BoundaryEntry
    boundary_effect: BoundaryEffectEntry

    @model_validator(mode="after")
    def _validate(self):
        old_set = set(self.old_districts)
        new_set = set(self.new_districts)
        if len(old_set) != len(self.old_districts):
            raise ValueError("duplicate old district code in package")
        if len(new_set) != len(self.new_districts):
            raise ValueError("duplicate new district code in package")

        for label, relations, codes in (
            ("old_relations", self.old_relations, old_set),
            ("new_relations", self.new_relations, new_set),
        ):
            for relation in relations:
                if not relation:
                    raise ValueError(f"empty relation in {label}")
                unknown = set(relation) - codes
                if unknown:
                    raise ValueError(
                        f"{label} references unknown districts: "
                        f"{sorted(unknown)}"
                    )
                if all(v == 0 for v in relation.values()):
                    raise ValueError(f"all-zero relation in {label}")

        # `districts` lists the codes that define the boundary on its own
        # side (old for the old boundary, new for the report boundary), while
        # the coefficients always live in the population space that is
        # actually evaluated (old for both baseline boundaries).
        for label, entry, coefficient_codes, district_codes in (
            ("old_boundary", self.old_boundary, old_set, old_set),
            (
                "new_boundary_baseline",
                self.new_boundary_baseline,
                old_set,
                new_set,
            ),
            (
                "new_boundary_report",
                self.new_boundary_report,
                new_set,
                new_set,
            ),
        ):
            unknown = set(entry.coefficients) - coefficient_codes
            if unknown:
                raise ValueError(
                    f"{label} has coefficients for unknown districts: "
                    f"{sorted(unknown)}"
                )
            unknown_districts = set(entry.districts) - district_codes
            if unknown_districts:
                raise ValueError(
                    f"{label} references unknown districts: "
                    f"{sorted(unknown_districts)}"
                )
        unknown = set(self.boundary_effect.coefficients) - old_set
        if unknown:
            raise ValueError(
                "boundary_effect has coefficients for unknown districts: "
                f"{sorted(unknown)}"
            )
        return self


# --------------------------------------------------------------------------
# Converter request/response (/analysis/convert)
# --------------------------------------------------------------------------
class AnalysisConvertRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    package: AnalysisPackage = Field(
        ...,
        validation_alias=AliasChoices(
            "package", "analysis_package", "boundary_package", "pkg"
        ),
    )
    old_populations: Dict[str, int] = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "old_populations",
            "baseline_populations",
            "before_populations",
            "old_population",
            "baseline_old_populations",
        ),
        description="Baseline (old) district populations already reported. "
        "Non-negative integers. Missing codes mean 'no data'.",
    )
    new_populations: Dict[str, int] = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "new_populations",
            "report_populations",
            "after_populations",
            "new_population",
            "report_new_populations",
        ),
        description="Report (new) district populations already reported. "
        "Non-negative integers. Missing codes mean 'no data'.",
    )
    populations: Optional[Dict[str, int]] = Field(
        default=None,
        description="Optional combined map; keys are matched against the "
        "package's old and new district codes.",
    )

    @model_validator(mode="after")
    def _validate(self):
        # A combined map is split by the package's code lists.
        if self.populations is not None:
            old_set = set(self.package.old_districts)
            new_set = set(self.package.new_districts)
            for code, value in self.populations.items():
                if code in old_set:
                    self.old_populations.setdefault(code, value)
                elif code in new_set:
                    self.new_populations.setdefault(code, value)
                else:
                    raise ValueError(
                        f"combined populations contain unknown code {code!r}"
                    )

        for label, populations, codes in (
            ("old_populations", self.old_populations, set(self.package.old_districts)),
            ("new_populations", self.new_populations, set(self.package.new_districts)),
        ):
            unknown = set(populations) - codes
            if unknown:
                raise ValueError(
                    f"{label} contains unknown district code(s): "
                    f"{sorted(unknown)}"
                )
            negatives = sorted(
                code for code, value in populations.items() if value < 0
            )
            if negatives:
                raise ValueError(
                    "populations must be non-negative integers: "
                    f"{negatives}"
                )
        return self


# --------------------------------------------------------------------------
# Converter response (/analysis/convert)
# --------------------------------------------------------------------------
class BoundaryTerm(BaseModel):
    """One boundary population evaluated from this request's data."""

    status: str = Field(..., description='"computed" or "missing"')
    population: Optional[int] = Field(
        None, description="Integer value, or null if not integral/available."
    )
    exact_population: Optional[str] = Field(
        None, description='Exact value as "p/q" when computable.'
    )
    coefficients: Dict[str, str] = Field(default_factory=dict)
    expression: Optional[str] = None
    missing_reason: Optional[str] = None
    missing: bool = False
    derivable: bool = True


class DifferenceResult(BaseModel):
    """One of the three independent differences."""

    code: str
    name: str
    status: str = Field(..., description='"computed" or "missing"')
    difference: Optional[int] = Field(
        None, description="Signed difference; may be negative."
    )
    value: Optional[int] = None
    expression: Optional[str] = Field(
        None, description="Coefficient expression of the difference."
    )
    formula: Optional[str] = Field(
        None, description="Period formula, e.g. (report) - (baseline)."
    )
    baseline_expression: Optional[str] = None
    report_expression: Optional[str] = None
    baseline_coefficients: Dict[str, str] = Field(default_factory=dict)
    report_coefficients: Dict[str, str] = Field(default_factory=dict)
    baseline_population: Optional[str] = None
    report_population: Optional[str] = None
    terms: Dict[str, BoundaryTerm] = Field(default_factory=dict)
    missing_reason: Optional[str] = Field(
        None,
        description="Why this difference is missing, if any. Never set for "
        "a computed difference.",
    )
    missing_reasons: List[str] = Field(default_factory=list)
    missing: bool = False
    derived_from_components: bool = True


class CheckedCoverageRelation(BaseModel):
    period: str
    relation: Dict[str, int]
    expression: str
    evaluated_value: int
    satisfied: bool


class MissingDifference(BaseModel):
    code: str
    missing_reason: Optional[str] = None


class AnalysisConvertResponse(BaseModel):
    total_change: DifferenceResult
    boundary_effect: DifferenceResult
    same_coverage_change: DifferenceResult
    same_scope_change: DifferenceResult
    same_caliber_change: DifferenceResult
    like_for_like_change: DifferenceResult
    boundary_impact: DifferenceResult
    changes: List[DifferenceResult]
    results: List[DifferenceResult] = Field(
        default_factory=list,
        description="Same three items as `changes`, in a results list.",
    )
    computed: List[DifferenceResult] = Field(
        default_factory=list,
        description='Only the differences with status "computed".',
    )
    differences: Dict[str, Optional[int]] = Field(
        ...,
        description="Convenience map: each difference code to its signed "
        "integer value, or null when that item is missing.",
    )
    summary: Dict[str, Optional[int]] = Field(
        default_factory=dict,
        description="Alias of `differences`.",
    )
    formulas: Dict[str, Dict[str, Optional[str]]] = Field(
        ...,
        description="Per-difference coefficient expression and period "
        "formula (null when the item is missing).",
    )
    missing: List[MissingDifference] = Field(
        default_factory=list,
        description='Codes of the differences marked "missing", with the '
        "reason. Empty when all three are computed.",
    )
    baseline: Dict[str, BoundaryTerm]
    report: Dict[str, BoundaryTerm]
    checked_relations: List[CheckedCoverageRelation]
    checked_old_relations: List[CheckedCoverageRelation]
    checked_new_relations: List[CheckedCoverageRelation]
    available_old_districts: List[str]
    available_new_districts: List[str]


class AnalysisErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    period: Optional[str] = None
