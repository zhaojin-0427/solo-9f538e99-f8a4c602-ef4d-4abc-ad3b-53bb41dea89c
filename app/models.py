"""Pydantic request/response models (shared by both FastAPI apps)."""
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


# --------------------------------------------------------------------------
# Handoff package builder (/build)
# --------------------------------------------------------------------------
class DistrictInput(BaseModel):
    code: str = Field(..., min_length=1, description="District code (old or new).")
    blocks: List[str] = Field(
        ..., description="IDs of the street blocks covered by this district."
    )


class BuildRequest(BaseModel):
    old_districts: List[DistrictInput] = Field(
        ..., description="Old districts with the street blocks each covers."
    )
    new_districts: List[DistrictInput] = Field(
        ..., description="New districts with the street blocks each covers."
    )

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
            for d in districts:
                if len(d.blocks) != len(set(d.blocks)):
                    raise ValueError(f"duplicate block id in district {d.code}")
        return self


class NewDistrictPackage(BaseModel):
    code: str
    derivable: bool = Field(
        ..., description="Whether this new district is exactly expressible "
        "in terms of old districts."
    )
    coefficients: Dict[str, str] = Field(
        default_factory=dict,
        description="Exact fraction coefficients keyed by old district code "
        '(e.g. "2/3"); whole numbers look like "2".',
    )


class HandoffPackage(BaseModel):
    schema_version: int = 1
    old_districts: List[str]
    new_districts: List[str]
    old_relations: List[Dict[str, int]] = Field(
        ...,
        description="Exact linear equations that old-district populations "
        "must satisfy. Each entry maps old code -> integer coefficient; "
        "the weighted sum must equal 0.",
    )
    new_district_relations: List[NewDistrictPackage]

    @model_validator(mode="after")
    def _validate(self):
        old_set = set(self.old_districts)
        if len(old_set) != len(self.old_districts):
            raise ValueError("duplicate old district code in package")
        new_set = set(self.new_districts)
        if len(new_set) != len(self.new_districts):
            raise ValueError("duplicate new district code in package")
        for rel in self.old_relations:
            if not rel:
                raise ValueError("empty old relation")
            unknown = set(rel) - old_set
            if unknown:
                raise ValueError(f"old relation references unknown districts: {sorted(unknown)}")
            if all(v == 0 for v in rel.values()):
                raise ValueError("all-zero old relation")
        for item in self.new_district_relations:
            if item.code not in new_set:
                raise ValueError(f"unknown new district {item.code}")
            unknown = set(item.coefficients) - old_set
            if unknown:
                raise ValueError(
                    f"coefficient for unknown old district: {sorted(unknown)}"
                )
        if {d.code for d in self.new_district_relations} != new_set:
            raise ValueError("new_district_relations must cover every new district exactly once")
        return self


# --------------------------------------------------------------------------
# Converter (/convert)
# --------------------------------------------------------------------------
class ConvertRequest(BaseModel):
    package: HandoffPackage
    old_populations: Dict[str, int] = Field(
        ..., description="Old-district populations currently available "
        "(non-negative integers). Missing codes mean 'no data'."
    )

    @model_validator(mode="after")
    def _validate(self):
        known = set(self.package.old_districts)
        unknown = set(self.old_populations) - known
        if unknown:
            raise ValueError(f"unknown old district code(s): {sorted(unknown)}")
        negatives = [c for c, v in self.old_populations.items() if v < 0]
        if negatives:
            raise ValueError(f"populations must be non-negative integers: {negatives}")
        return self


class CheckedRelation(BaseModel):
    relation: Dict[str, int]
    expression: str
    evaluated_value: int
    satisfied: bool


class NewDistrictResult(BaseModel):
    code: str
    status: str = Field(..., description='"computed" or "missing"')
    population: Optional[int] = None
    coefficients: Dict[str, str] = Field(default_factory=dict)
    expression: Optional[str] = None
    missing_reason: Optional[str] = None


class ConvertResponse(BaseModel):
    available_old_districts: List[str]
    checked_relations: List[CheckedRelation]
    computed: List[NewDistrictResult]
    missing: List[NewDistrictResult]
    results: List[NewDistrictResult]


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
