"""Pure domain logic: handoff-package building and population conversion.

The math uses exact :class:`fractions.Fraction` arithmetic throughout.

Model
-----
Let ``x_b`` be the (unknown, non-negative integer) population of street
block ``b``. With 0/1 incidence matrices

* ``A`` (blocks x old districts):  old population   ``p = A x``
* ``B`` (blocks x new districts):  new population   ``q = B x``

the exact linear relations between populations are derived without ever
knowing the block populations themselves.
"""
from fractions import Fraction
from typing import Any, Dict, List, Tuple

from .expressions import format_expression, format_fraction, parse_fraction
from .rationals import Vec, nullspace, primitive_integer, solve


class ConversionError(ValueError):
    """Raised when a whole conversion request must be rejected."""


# --------------------------------------------------------------------------
# Package builder
# --------------------------------------------------------------------------
def build_package(
    old_districts: List[Dict[str, Any]],
    new_districts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a block-free JSON handoff package.

    Each district is ``{"code": str, "blocks": [block_id, ...]}``.
    A block may be covered by any number of districts.
    """
    old_codes = [d["code"] for d in old_districts]
    new_codes = [d["code"] for d in new_districts]
    n_old = len(old_codes)

    blocks = sorted(
        {b for d in old_districts + new_districts for b in d["blocks"]}
    )

    def incidence(districts: List[Dict[str, Any]]):
        return [
            [
                Fraction(1 if block in district["blocks"] else 0)
                for district in districts
            ]
            for block in blocks
        ]

    a = incidence(old_districts)  # blocks x old
    b = incidence(new_districts)  # blocks x new

    # ker(A): every vector k with A k = 0 is an exact equation
    # sum_j k_j * p_j = 0 between old-district populations.
    kernel = nullspace(a, n_old)
    old_relations: List[Dict[str, int]] = []
    for k in kernel:
        ints = primitive_integer(k)
        relation = {code: val for code, val in zip(old_codes, ints) if val != 0}
        old_relations.append(relation)

    # A c_j = b_j  =>  q_j = c_j . p  whenever the system is consistent.
    new_relations = []
    for j, code in enumerate(new_codes):
        col = [row[j] for row in b]
        particular = solve(a, col, n_old)
        if particular is None:
            new_relations.append(
                {"code": code, "derivable": False, "coefficients": {}}
            )
        else:
            coefficients = {
                old_codes[i]: format_fraction(particular[i])
                for i in range(n_old)
                if particular[i] != 0
            }
            new_relations.append(
                {"code": code, "derivable": True, "coefficients": coefficients}
            )

    return {
        "schema_version": 1,
        "old_districts": old_codes,
        "new_districts": new_codes,
        "old_relations": old_relations,
        "new_district_relations": new_relations,
    }


# --------------------------------------------------------------------------
# Converter
# --------------------------------------------------------------------------
def _expand_relation(relation: Dict[str, int], old_codes: List[str]) -> Vec:
    return [Fraction(relation.get(code, 0)) for code in old_codes]


def _support(vector: Vec) -> List[int]:
    return [i for i, v in enumerate(vector) if v != 0]


def _vector_on_available(
    particular: Vec,
    kernel_basis: List[Vec],
    n_vars: int,
    available_idx,
) -> Tuple[bool, Vec]:
    """Find a coefficient vector supported on the available districts.

    All coefficient vectors describing the same new district differ from
    ``particular`` by a member of ``ker(A)``, which is spanned by the rows
    of ``kernel_basis`` (the package's old relations). Hence every solution
    has the form ``d = particular + M^T alpha`` for some rational ``alpha``.
    Requiring ``d`` to vanish on every district without data gives a linear
    system for ``alpha``; consistency is exactly the computability test.
    """
    r = len(kernel_basis)
    unavailable = [i for i in range(n_vars) if i not in available_idx]
    constraint = [
        [kernel_basis[j][i] for j in range(r)] for i in unavailable
    ]
    rhs = [-particular[i] for i in unavailable]
    alpha = solve(constraint, rhs, r)
    if alpha is None:
        return False, particular
    chosen = [
        particular[i]
        + sum(alpha[j] * kernel_basis[j][i] for j in range(r))
        for i in range(n_vars)
    ]
    return True, chosen


def convert(package: Dict[str, Any], old_populations: Dict[str, int]) -> Dict[str, Any]:
    """Convert known old-district populations into new-district populations.

    Computability is judged solely from the districts having data *in this
    request* and the algebraic relations in the package. On any violation
    (contradicted coverage relation, negative or fractional result) raises
    :class:`ConversionError` and nothing else is returned.
    """
    old_codes: List[str] = package["old_districts"]
    new_codes: List[str] = package["new_districts"]
    n_old = len(old_codes)
    available = [code for code in old_codes if code in old_populations]
    available_idx = {old_codes.index(c) for c in available}
    populations = [Fraction(old_populations.get(code, 0)) for code in old_codes]

    # --- 1. Exact equations between old districts that are fully supported
    #     by the districts for which data was supplied this time. ----------
    kernel_basis: List[Vec] = [
        _expand_relation(rel, old_codes) for rel in package["old_relations"]
    ]
    checked_relations = []
    for k in kernel_basis:
        support = _support(k)
        if not set(support).issubset(available_idx):
            continue
        value = sum((k[i] * populations[i] for i in support), Fraction(0))
        expr = format_expression({old_codes[i]: k[i] for i in support})
        checked_relations.append(
            {
                "relation": {old_codes[i]: int(k[i]) for i in support},
                "expression": expr,
                "evaluated_value": int(value),
                "satisfied": value == 0,
            }
        )
        if value != 0:
            raise ConversionError(
                "old-district populations violate the coverage relation "
                f"{expr} = 0 (evaluates to {int(value)})"
            )

    results: List[Dict[str, Any]] = []
    for item in package["new_district_relations"]:
        code = item["code"]
        if not item["derivable"]:
            results.append(
                {
                    "code": code,
                    "status": "missing",
                    "population": None,
                    "coefficients": {},
                    "expression": None,
                    "missing_reason": "not_expressible_from_old_districts",
                }
            )
            continue

        particular = [
            parse_fraction(item["coefficients"][old_codes[i]])
            if old_codes[i] in item["coefficients"]
            else Fraction(0)
            for i in range(n_old)
        ]
        ok, chosen = _vector_on_available(
            particular, kernel_basis, n_old, available_idx
        )
        if not ok:
            nonzero = {
                old_codes[i]: particular[i]
                for i in range(n_old)
                if particular[i] != 0
            }
            results.append(
                {
                    "code": code,
                    "status": "missing",
                    "population": None,
                    "coefficients": {
                        c: format_fraction(v) for c, v in nonzero.items()
                    },
                    "expression": format_expression(nonzero),
                    "missing_reason": "insufficient_known_old_districts",
                }
            )
            continue

        value = sum(
            (chosen[i] * populations[i] for i in range(n_old)), Fraction(0)
        )
        if value < 0:
            raise ConversionError(
                f"new district {code} converts to a negative population "
                f"({format_fraction(value)})"
            )
        if value.denominator != 1:
            raise ConversionError(
                f"new district {code} converts to a non-integer population "
                f"({format_fraction(value)})"
            )

        nonzero = {
            old_codes[i]: chosen[i]
            for i in range(n_old)
            if chosen[i] != 0
        }
        results.append(
            {
                "code": code,
                "status": "computed",
                "population": int(value),
                "coefficients": {
                    c: format_fraction(v) for c, v in nonzero.items()
                },
                "expression": format_expression(nonzero),
                "missing_reason": None,
            }
        )

    computed = [r for r in results if r["status"] == "computed"]
    missing = [r for r in results if r["status"] == "missing"]
    return {
        "available_old_districts": available,
        "checked_relations": checked_relations,
        "computed": computed,
        "missing": missing,
        "results": results,
    }
