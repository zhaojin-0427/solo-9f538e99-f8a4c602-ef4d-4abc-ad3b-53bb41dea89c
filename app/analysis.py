"""County-boundary analysis: package building and cross-period decomposition.

The yearbook needs the population change caused by a county-boundary redraw
to be reported separately.  Two boundaries are involved:

* the **baseline old boundary** — the blocks covered by a group of old
  districts, and
* the **report new boundary** — the blocks covered by a group of new
  districts.

Within each group the blocks are *unioned* (duplicates across districts are
counted once), so a boundary population is a linear functional of the block
populations, not a blind sum of overlapping district populations.

Because no block-level data is retained, the analysis package stores only
exact rational coefficient vectors over district codes.  Let ``A`` be the
block x old-district incidence matrix, ``B`` the block x new-district
incidence matrix, ``u_old`` the 0/1 indicator of the old-boundary blocks and
``u_new`` the indicator of the new-boundary blocks:

* ``A c = u_old``            -> baseline old-boundary population  ``c . p``
* ``A c = u_new``            -> baseline new-boundary population  ``c . p``
* ``B d = u_new``            -> report new-boundary population    ``d . q``
* ``A c = u_new - u_old``    -> boundary effect directly           ``c . p``

The last equation is important: even when neither boundary can be evaluated
individually from the old districts that happen to be reported this time, the
difference may still be exactly derivable because the unavailable coefficients
cancel.  ``p`` are the baseline old-district populations, ``q`` the report
new-district populations.

The three reported differences are

* ``total_change          = report_new_boundary - baseline_old_boundary``
* ``boundary_effect       = baseline_new_boundary - baseline_old_boundary``
* ``same_coverage_change  = report_new_boundary - baseline_new_boundary``

and each one is judged independently.  All arithmetic uses
:class:`fractions.Fraction`; no floating point is used.
"""
from fractions import Fraction
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .engine import (
    ConversionError,
    _expand_relation,
    _support,
    _vector_on_available,
)
from .expressions import format_expression, format_fraction, parse_fraction
from .rationals import Mat, Vec, nullspace, primitive_integer, solve

# Missing-data vocabulary, parallel to the v1 converter.
NOT_EXPRESSIBLE_OLD = "not_expressible_from_old_districts"
NOT_EXPRESSIBLE_NEW = "not_expressible_from_new_districts"
INSUFFICIENT_OLD = "insufficient_known_old_districts"
INSUFFICIENT_NEW = "insufficient_known_new_districts"


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _dedupe(codes: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for code in codes:
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


def _incidence(districts: List[Dict[str, Any]], blocks: List[str]) -> Mat:
    return [
        [
            Fraction(1 if block in district["blocks"] else 0)
            for district in districts
        ]
        for block in blocks
    ]


def _relation_rows(kernel: List[Vec], codes: List[str]) -> List[Dict[str, int]]:
    rows: List[Dict[str, int]] = []
    for vector in kernel:
        ints = primitive_integer(vector)
        rows.append(
            {code: value for code, value in zip(codes, ints) if value != 0}
        )
    return rows


def _coefficient_map(
    vector: Sequence[Fraction], codes: Sequence[str]
) -> Dict[str, str]:
    return {
        codes[i]: format_fraction(vector[i])
        for i in range(len(codes))
        if vector[i] != 0
    }


def _coefficient_fractions(
    mapping: Dict[str, str], codes: Sequence[str]
) -> Vec:
    return [
        parse_fraction(mapping[codes[i]])
        if codes[i] in mapping
        else Fraction(0)
        for i in range(len(codes))
    ]


def _boundary_entry(
    districts: List[str],
    vector: Optional[Vec],
    codes: Sequence[str],
) -> Dict[str, Any]:
    return {
        "districts": districts,
        "derivable": vector is not None,
        "coefficients": (
            {} if vector is None else _coefficient_map(vector, codes)
        ),
    }


# --------------------------------------------------------------------------
# Package builder
# --------------------------------------------------------------------------
def build_analysis_package(
    old_districts: List[Dict[str, Any]],
    new_districts: List[Dict[str, Any]],
    before_districts: List[str],
    after_districts: List[str],
) -> Dict[str, Any]:
    """Build the offline-reusable analysis package (schema version 2).

    ``before_districts`` are old-district codes whose blocks form the
    baseline old boundary; ``after_districts`` are new-district codes whose
    blocks form the report new boundary.  Blocks are deduplicated inside
    each group.  The returned package contains no block identifiers and no
    per-block membership.
    """
    old_codes = [d["code"] for d in old_districts]
    new_codes = [d["code"] for d in new_districts]
    before = _dedupe(before_districts)
    after = _dedupe(after_districts)

    blocks = sorted(
        {b for d in old_districts + new_districts for b in d["blocks"]}
    )
    a = _incidence(old_districts, blocks)
    b = _incidence(new_districts, blocks)

    old_relations = _relation_rows(nullspace(a, len(old_codes)), old_codes)
    new_relations = _relation_rows(nullspace(b, len(new_codes)), new_codes)

    old_by_code = {d["code"]: d for d in old_districts}
    new_by_code = {d["code"]: d for d in new_districts}
    old_blocks: Set[str] = set()
    for code in before:
        old_blocks.update(old_by_code[code]["blocks"])
    new_blocks: Set[str] = set()
    for code in after:
        new_blocks.update(new_by_code[code]["blocks"])

    u_old = [Fraction(1 if block in old_blocks else 0) for block in blocks]
    u_new = [Fraction(1 if block in new_blocks else 0) for block in blocks]
    u_diff = [u_new[i] - u_old[i] for i in range(len(blocks))]

    old_boundary = solve(a, u_old, len(old_codes))
    new_baseline = solve(a, u_new, len(old_codes))
    new_report = solve(b, u_new, len(new_codes))
    effect = solve(a, u_diff, len(old_codes))

    return {
        "schema_version": 2,
        "kind": "boundary_analysis",
        "old_districts": old_codes,
        "new_districts": new_codes,
        "old_relations": old_relations,
        "new_relations": new_relations,
        "old_boundary": _boundary_entry(before, old_boundary, old_codes),
        "new_boundary_baseline": _boundary_entry(
            after, new_baseline, old_codes
        ),
        "new_boundary_report": _boundary_entry(
            after, new_report, new_codes
        ),
        "boundary_effect": {
            "derivable": effect is not None,
            "coefficients": (
                {} if effect is None else _coefficient_map(effect, old_codes)
            ),
        },
    }


# --------------------------------------------------------------------------
# Converter internals
# --------------------------------------------------------------------------
def _check_relations(
    kernel: List[Vec],
    codes: List[str],
    populations: List[Fraction],
    available_idx: Set[int],
    period: str,
) -> List[Dict[str, Any]]:
    """Validate every relation fully supported by the reported districts."""
    checked: List[Dict[str, Any]] = []
    for vector in kernel:
        support = _support(vector)
        if not set(support).issubset(available_idx):
            continue
        value = sum(
            (vector[i] * populations[i] for i in support), Fraction(0)
        )
        expression = format_expression(
            {codes[i]: vector[i] for i in support}
        )
        checked.append(
            {
                "period": period,
                "relation": {codes[i]: int(vector[i]) for i in support},
                "expression": expression,
                "evaluated_value": int(value),
                "satisfied": value == 0,
            }
        )
        if value != 0:
            raise ConversionError(
                f"{period}-district populations violate the coverage "
                f"relation {expression} = 0 (evaluates to {int(value)})"
            )
    return checked


def _missing_term(
    *,
    coefficients: Dict[str, str],
    codes: List[str],
    side: str,
    derivable: bool,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    particular = _coefficient_fractions(coefficients, codes)
    expression = format_expression(
        {
            codes[i]: particular[i]
            for i in range(len(codes))
            if particular[i] != 0
        }
    )
    return {
        "status": "missing",
        "population": None,
        "exact_population": None,
        "coefficients": _coefficient_map(particular, codes),
        "expression": expression if derivable else None,
        "missing_reason": reason,
        "missing": True,
        "derivable": derivable,
        "side": side,
    }


def _evaluate(
    *,
    derivable: bool,
    coefficients: Dict[str, str],
    codes: List[str],
    kernel: List[Vec],
    populations: List[Fraction],
    available_idx: Set[int],
    side: str,
) -> Dict[str, Any]:
    """Evaluate one stored boundary functional on the reported populations.

    ``side`` is ``"old"`` or ``"new"`` and only selects the missing-data
    vocabulary.  Availability is judged solely from this request's data.
    """
    if not derivable:
        return _missing_term(
            coefficients=coefficients,
            codes=codes,
            side=side,
            derivable=False,
            reason=(
                NOT_EXPRESSIBLE_OLD if side == "old" else NOT_EXPRESSIBLE_NEW
            ),
        )

    particular = _coefficient_fractions(coefficients, codes)
    ok, chosen = _vector_on_available(
        particular, kernel, len(codes), available_idx
    )
    if not ok:
        return _missing_term(
            coefficients=coefficients,
            codes=codes,
            side=side,
            derivable=True,
            reason=INSUFFICIENT_OLD if side == "old" else INSUFFICIENT_NEW,
        )

    value = sum(
        (chosen[i] * populations[i] for i in range(len(codes))), Fraction(0)
    )
    nonzero = {
        codes[i]: chosen[i] for i in range(len(codes)) if chosen[i] != 0
    }
    return {
        "status": "computed",
        "population": int(value) if value.denominator == 1 else None,
        "exact_population": format_fraction(value),
        "value": value,
        "coefficients": {
            code: format_fraction(coef) for code, coef in nonzero.items()
        },
        "expression": format_expression(nonzero),
        "missing_reason": None,
        "missing": False,
        "derivable": True,
        "side": side,
    }


def _term_payload(term: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": term["status"],
        "population": term["population"],
        "exact_population": term["exact_population"],
        "coefficients": term["coefficients"],
        "expression": term["expression"],
        "missing_reason": term["missing_reason"],
        "missing": term["missing"],
        "derivable": term["derivable"],
    }


def _combine_terms(
    report: Dict[str, Any], baseline: Dict[str, Any]
) -> Tuple[Optional[str], Optional[str]]:
    """Return (coefficient expression, parenthesised period formula)."""
    combined: Dict[str, Fraction] = {}
    for code, text in report["coefficients"].items():
        combined[code] = combined.get(code, Fraction(0)) + parse_fraction(text)
    for code, text in baseline["coefficients"].items():
        combined[code] = combined.get(code, Fraction(0)) - parse_fraction(text)
    combined = {code: v for code, v in combined.items() if v != 0}
    expression = format_expression(combined)
    formula = f"({report['expression']}) - ({baseline['expression']})"
    return expression, formula


def _missing_reason(
    terms: Sequence[Dict[str, Any]]
) -> Tuple[Optional[str], List[str]]:
    reasons: List[str] = []
    for term in terms:
        reason = term.get("missing_reason")
        if (
            term["status"] == "missing"
            and reason
            and reason not in reasons
        ):
            reasons.append(reason)
    return ("; ".join(reasons) if reasons else None), reasons


def _change_item(
    *,
    code: str,
    report: Dict[str, Any],
    baseline: Dict[str, Any],
    report_key: str,
    baseline_key: str,
    expressions: Tuple[Optional[str], Optional[str]],
) -> Dict[str, Any]:
    """Assemble one of the three independent differences.

    ``report`` and ``baseline`` are the two operands of ``report - baseline``.
    If either operand is missing this time the whole difference is missing.
    """
    terms = {baseline_key: _term_payload(baseline)}
    terms[report_key] = _term_payload(report)

    if report["status"] == "missing" or baseline["status"] == "missing":
        reason, reasons = _missing_reason([baseline, report])
        return {
            "code": code,
            "name": code,
            "status": "missing",
            "difference": None,
            "value": None,
            "expression": None,
            "formula": None,
            "baseline_expression": baseline["expression"],
            "report_expression": report["expression"],
            "baseline_coefficients": baseline["coefficients"],
            "report_coefficients": report["coefficients"],
            "baseline_population": baseline["exact_population"],
            "report_population": report["exact_population"],
            "terms": terms,
            "missing_reason": reason,
            "missing_reasons": reasons,
            "missing": True,
            "derived_from_components": False,
        }

    value = report["value"] - baseline["value"]
    if value.denominator != 1:
        raise ConversionError(
            f"{code} is not an integer difference "
            f"({format_fraction(value)})"
        )
    return {
        "code": code,
        "name": code,
        "status": "computed",
        "difference": int(value),
        "value": int(value),
        "expression": expressions[0],
        "formula": expressions[1],
        "baseline_expression": baseline["expression"],
        "report_expression": report["expression"],
        "baseline_coefficients": baseline["coefficients"],
        "report_coefficients": report["coefficients"],
        "baseline_population": baseline["exact_population"],
        "report_population": report["exact_population"],
        "terms": terms,
        "missing_reason": None,
        "missing_reasons": [],
        "missing": False,
        "derived_from_components": True,
    }


def _direct_effect_item(
    *,
    effect: Dict[str, Any],
    new_baseline: Dict[str, Any],
    old_baseline: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Build the boundary effect from the stored ``u_new - u_old`` relation.

    Returns ``None`` when the direct relation cannot be evaluated this time,
    in which case the caller falls back to combining the two boundaries.
    """
    if effect["status"] != "computed":
        return None
    value = effect["value"]
    assert value is not None
    if value.denominator != 1:
        raise ConversionError(
            "boundary_effect is not an integer difference "
            f"({format_fraction(value)})"
        )
    if (
        new_baseline["status"] == "computed"
        and old_baseline["status"] == "computed"
    ):
        formula = (
            f"({new_baseline['expression']}) - "
            f"({old_baseline['expression']})"
        )
    else:
        formula = effect["expression"]
    return {
        "code": "boundary_effect",
        "name": "boundary_effect",
        "status": "computed",
        "difference": int(value),
        "value": int(value),
        "expression": effect["expression"],
        "formula": formula,
        "baseline_expression": old_baseline["expression"],
        "report_expression": new_baseline["expression"],
        "baseline_coefficients": old_baseline["coefficients"],
        "report_coefficients": new_baseline["coefficients"],
        "baseline_population": old_baseline["exact_population"],
        "report_population": new_baseline["exact_population"],
        "terms": {
            "baseline_old_boundary": _term_payload(old_baseline),
            "baseline_new_boundary": _term_payload(new_baseline),
        },
        "missing_reason": None,
        "missing_reasons": [],
        "missing": False,
        "derived_from_components": False,
    }


def _effect_missing_item(
    new_baseline: Dict[str, Any], old_baseline: Dict[str, Any]
) -> Dict[str, Any]:
    reason, reasons = _missing_reason([old_baseline, new_baseline])
    return {
        "code": "boundary_effect",
        "name": "boundary_effect",
        "status": "missing",
        "difference": None,
        "value": None,
        "expression": None,
        "formula": None,
        "baseline_expression": old_baseline["expression"],
        "report_expression": new_baseline["expression"],
        "baseline_coefficients": old_baseline["coefficients"],
        "report_coefficients": new_baseline["coefficients"],
        "baseline_population": old_baseline["exact_population"],
        "report_population": new_baseline["exact_population"],
        "terms": {
            "baseline_old_boundary": _term_payload(old_baseline),
            "baseline_new_boundary": _term_payload(new_baseline),
        },
        "missing_reason": reason,
        "missing_reasons": reasons,
        "missing": True,
        "derived_from_components": False,
    }


def convert_analysis(
    package: Dict[str, Any],
    old_populations: Dict[str, int],
    new_populations: Dict[str, int],
) -> Dict[str, Any]:
    """Compute the three independent differences for one request.

    The request fails as a whole (and nothing else is reported) when a
    checkable coverage relation in either period is contradicted, or when a
    computable difference is not an integer.  A non-integer boundary
    component is not by itself an error; only the reported differences are
    required to be integral, so a fractional component that cancels in a
    difference still yields a valid integer result.
    """
    old_codes: List[str] = package["old_districts"]
    new_codes: List[str] = package["new_districts"]

    available_old = [code for code in old_codes if code in old_populations]
    available_new = [code for code in new_codes if code in new_populations]
    available_old_idx = {old_codes.index(code) for code in available_old}
    available_new_idx = {new_codes.index(code) for code in available_new}

    old_pop = [Fraction(old_populations.get(code, 0)) for code in old_codes]
    new_pop = [Fraction(new_populations.get(code, 0)) for code in new_codes]

    old_kernel = [
        _expand_relation(relation, old_codes)
        for relation in package["old_relations"]
    ]
    new_kernel = [
        _expand_relation(relation, new_codes)
        for relation in package["new_relations"]
    ]

    checked: List[Dict[str, Any]] = []
    checked.extend(
        _check_relations(
            old_kernel, old_codes, old_pop, available_old_idx, "old"
        )
    )
    checked.extend(
        _check_relations(
            new_kernel, new_codes, new_pop, available_new_idx, "new"
        )
    )

    old_baseline = _evaluate(
        derivable=package["old_boundary"]["derivable"],
        coefficients=package["old_boundary"]["coefficients"],
        codes=old_codes,
        kernel=old_kernel,
        populations=old_pop,
        available_idx=available_old_idx,
        side="old",
    )
    new_baseline = _evaluate(
        derivable=package["new_boundary_baseline"]["derivable"],
        coefficients=package["new_boundary_baseline"]["coefficients"],
        codes=old_codes,
        kernel=old_kernel,
        populations=old_pop,
        available_idx=available_old_idx,
        side="old",
    )
    new_report = _evaluate(
        derivable=package["new_boundary_report"]["derivable"],
        coefficients=package["new_boundary_report"]["coefficients"],
        codes=new_codes,
        kernel=new_kernel,
        populations=new_pop,
        available_idx=available_new_idx,
        side="new",
    )
    effect = _evaluate(
        derivable=package["boundary_effect"]["derivable"],
        coefficients=package["boundary_effect"]["coefficients"],
        codes=old_codes,
        kernel=old_kernel,
        populations=old_pop,
        available_idx=available_old_idx,
        side="old",
    )

    # Boundary effect = baseline new boundary - baseline old boundary.
    # When both components are computable, subtract them so the result is
    # automatically consistent with the other two differences.  When one is
    # not, fall back to the stored direct (u_new - u_old) derivative, which
    # may still be exactly evaluable because the unavailable coefficients
    # cancel.
    if (
        new_baseline["status"] == "computed"
        and old_baseline["status"] == "computed"
    ):
        boundary_effect = _change_item(
            code="boundary_effect",
            report=new_baseline,
            baseline=old_baseline,
            report_key="baseline_new_boundary",
            baseline_key="baseline_old_boundary",
            expressions=_combine_terms(new_baseline, old_baseline),
        )
    else:
        boundary_effect = _direct_effect_item(
            effect=effect,
            new_baseline=new_baseline,
            old_baseline=old_baseline,
        ) or _effect_missing_item(new_baseline, old_baseline)

    total_change = _change_item(
        code="total_change",
        report=new_report,
        baseline=old_baseline,
        report_key="report_new_boundary",
        baseline_key="baseline_old_boundary",
        expressions=_combine_terms(new_report, old_baseline),
    )
    same_coverage_change = _change_item(
        code="same_coverage_change",
        report=new_report,
        baseline=new_baseline,
        report_key="report_new_boundary",
        baseline_key="baseline_new_boundary",
        expressions=_combine_terms(new_report, new_baseline),
    )

    changes = [total_change, boundary_effect, same_coverage_change]

    baseline = {
        "old_boundary": _term_payload(old_baseline),
        "new_boundary": _term_payload(new_baseline),
    }
    report = {"new_boundary": _term_payload(new_report)}

    differences = {
        "total_change": total_change["difference"],
        "boundary_effect": boundary_effect["difference"],
        "same_coverage_change": same_coverage_change["difference"],
    }
    formulas = {
        "total_change": {
            "expression": total_change["expression"],
            "formula": total_change["formula"],
        },
        "boundary_effect": {
            "expression": boundary_effect["expression"],
            "formula": boundary_effect["formula"],
        },
        "same_coverage_change": {
            "expression": same_coverage_change["expression"],
            "formula": same_coverage_change["formula"],
        },
    }
    missing_items = [
        {"code": item["code"], "missing_reason": item["missing_reason"]}
        for item in changes
        if item["status"] == "missing"
    ]

    return {
        "total_change": total_change,
        "boundary_effect": boundary_effect,
        "same_coverage_change": same_coverage_change,
        # Common synonyms kept for interoperability with report writers.
        "same_scope_change": same_coverage_change,
        "same_caliber_change": same_coverage_change,
        "like_for_like_change": same_coverage_change,
        "boundary_impact": boundary_effect,
        "changes": changes,
        "results": changes,
        "computed": [item for item in changes if item["status"] == "computed"],
        "differences": differences,
        "summary": differences,
        "formulas": formulas,
        "missing": missing_items,
        "baseline": baseline,
        "report": report,
        "checked_relations": checked,
        "checked_old_relations": [
            row for row in checked if row["period"] == "old"
        ],
        "checked_new_relations": [
            row for row in checked if row["period"] == "new"
        ],
        "available_old_districts": available_old,
        "available_new_districts": available_new,
    }
