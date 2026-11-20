"""End-to-end tests through both FastAPI apps (uses FastAPI TestClient)."""
import pytest
from fastapi.testclient import TestClient

from app.builder_app import app as builder_app
from app.converter_app import app as converter_app

builder = TestClient(builder_app)
converter = TestClient(converter_app)


def build(old, new):
    resp = builder.post(
        "/build", json={"old_districts": old, "new_districts": new}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def convert_with(package, populations):
    return converter.post(
        "/convert", json={"package": package, "old_populations": populations}
    )


# --------------------------------------------------------------------------
# 1. Plain disjoint partition: new districts are unions of old ones.
# --------------------------------------------------------------------------
def test_disjoint_partition_exact():
    old = [
        {"code": "O1", "blocks": ["b1", "b2"]},
        {"code": "O2", "blocks": ["b3"]},
        {"code": "O3", "blocks": ["b4"]},
    ]
    new = [
        {"code": "N1", "blocks": ["b1", "b2", "b3"]},
        {"code": "N2", "blocks": ["b4"]},
    ]
    pkg = build(old, new)
    # No linear dependencies between old districts -> no stored relations,
    # and the package must not leak any block identifiers.
    assert pkg["old_relations"] == []
    text = str(pkg)
    assert "b1" not in text and "b3" not in text
    assert {d["code"] for d in pkg["new_district_relations"]} == {"N1", "N2"}
    n1 = next(d for d in pkg["new_district_relations"] if d["code"] == "N1")
    assert n1["derivable"] is True
    assert n1["coefficients"] == {"O1": "1", "O2": "1"}

    resp = convert_with(pkg, {"O1": 10, "O2": 5, "O3": 7})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {c["code"]: c["population"] for c in body["computed"]} == {
        "N1": 15,
        "N2": 7,
    }
    assert body["missing"] == []
    n1_result = next(c for c in body["results"] if c["code"] == "N1")
    assert n1_result["expression"] == "O1 + O2"


# --------------------------------------------------------------------------
# 2. Overlapping old districts create an exact old-district equation.
# --------------------------------------------------------------------------
def test_overlapping_old_districts_relation():
    # O1 = x1 + x2, O2 = x2 + x3  =>  no equation with only these two.
    # Add O3 = x1 + x3:  O1 + O2 - O3 = 2 x2 ... build a true relation:
    # O4 = x1+x2+x3 gives O1 + O2 + O3 = 2 * O4.
    old = [
        {"code": "O1", "blocks": ["x1", "x2"]},
        {"code": "O2", "blocks": ["x2", "x3"]},
        {"code": "O3", "blocks": ["x1", "x3"]},
        {"code": "O4", "blocks": ["x1", "x2", "x3"]},
    ]
    new = [{"code": "N1", "blocks": ["x1", "x2", "x3"]}]
    pkg = build(old, new)
    rels = [tuple(sorted(r.items())) for r in pkg["old_relations"]]
    assert (("O1", 1), ("O2", 1), ("O3", 1), ("O4", -2)) in rels

    # Satisfied relation: x=(2,3,4) -> O1=5 O2=7 O3=6 O4=9 ; 5+7+6=18=2*9
    resp = convert_with(pkg, {"O1": 5, "O2": 7, "O3": 6, "O4": 9})
    assert resp.status_code == 200
    assert resp.json()["computed"][0]["population"] == 9

    # Violated relation -> whole request is an error.
    resp = convert_with(pkg, {"O1": 5, "O2": 7, "O3": 6, "O4": 8})
    assert resp.status_code == 400
    assert resp.json()["error"] == "conversion_failed"
    checked = convert_with(pkg, {"O1": 5, "O2": 7, "O3": 6, "O4": 9}).json()
    assert checked["checked_relations"][0]["satisfied"] is True


# --------------------------------------------------------------------------
# 3. Fractional coefficients, non-integer conversion result must fail.
# --------------------------------------------------------------------------
def test_fractional_coefficients_and_non_integer_error():
    # Two old districts split a block: O1 = x1 + x2, O2 = x2 (x2 shared).
    # New N1 = x1 + 2 x2 = O1 + O2 (integer here). Build a half case:
    # N2 = blocks x1,x2 duplicated coverage is 0/1 only, so use:
    # O1=x1+x2, O2=x1  => N1=x2 = O1 - O2 ; N2=x1+x2+x3 with O3=x3:
    old = [
        {"code": "O1", "blocks": ["x1", "x2"]},
        {"code": "O2", "blocks": ["x1"]},
        {"code": "O3", "blocks": ["x3"]},
    ]
    new = [
        {"code": "N_diff", "blocks": ["x2"]},
        {"code": "N_all", "blocks": ["x1", "x2", "x3"]},
    ]
    pkg = build(old, new)
    nd = next(d for d in pkg["new_district_relations"] if d["code"] == "N_diff")
    assert nd["coefficients"] == {"O1": "1", "O2": "-1"}
    na = next(d for d in pkg["new_district_relations"] if d["code"] == "N_all")
    assert na["coefficients"] == {"O1": "1", "O3": "1"}

    resp = convert_with(pkg, {"O1": 3, "O2": 10, "O3": 4})
    assert resp.status_code == 400
    assert "negative" in resp.json()["detail"]

    # Classic halves: O1=x1+x2 (shared block x2), O2=x2+x3, N1=x1+x2+x3.
    # N1 = O1 + O2 - x2 needs x2 ... instead: 3 old on one block yields
    # fractions: O1=O2=O3 cover block z, N1 covers z as well.
    old2 = [
        {"code": "O1", "blocks": ["z"]},
        {"code": "O2", "blocks": ["z"]},
        {"code": "O3", "blocks": ["z"]},
    ]
    new2 = [{"code": "N1", "blocks": ["z"]}]
    pkg2 = build(old2, new2)
    n1 = pkg2["new_district_relations"][0]
    # One valid particular solution, e.g. N1 = O1, with relations O1-O2=0...
    assert n1["derivable"] is True
    resp = convert_with(pkg2, {"O1": 1, "O2": 1, "O3": 1})
    assert resp.status_code == 200
    assert resp.json()["computed"][0]["population"] == 1
    # Contradictory equalities between old districts -> error.
    resp = convert_with(pkg2, {"O1": 1, "O2": 2, "O3": 1})
    assert resp.status_code == 400


def test_half_coefficient_non_integer_result():
    # O1=x1+x2, O2=x1+x2 (identical coverage). N1=x1+x2: builder returns
    # SOME rational particular solution (here N1 = O1).
    old = [
        {"code": "O1", "blocks": ["x1", "x2"]},
        {"code": "O2", "blocks": ["x1", "x2"]},
    ]
    new = [{"code": "N1", "blocks": ["x1", "x2"]}]
    pkg = build(old, new)
    assert pkg["old_relations"] == [{"O1": 1, "O2": -1}]
    resp = convert_with(pkg, {"O1": 3, "O2": 3})
    assert resp.status_code == 200
    assert resp.json()["computed"][0]["population"] == 3


def test_determinant_two_integer_formula():
    # 3x3 block-incidence matrix with determinant -2, but the particular
    # solution for target x1 happens to be integral: x1 = O2 - O3.
    # A = [[1,1,0],[1,1,1],[0,1,1]]
    old = [
        {"code": "O1", "blocks": ["x1", "x2"]},          # 1 1 0
        {"code": "O2", "blocks": ["x1", "x2", "x3"]},    # 1 1 1
        {"code": "O3", "blocks": ["x2", "x3"]},          # 0 1 1
    ]
    new = [{"code": "N1", "blocks": ["x1"]}]
    pkg = build(old, new)
    n1 = pkg["new_district_relations"][0]
    assert n1["derivable"] is True
    assert n1["coefficients"] == {"O2": "1", "O3": "-1"}

    # Realizable populations x=(2,3,4): O1=5, O2=9, O3=7 -> N1=2.
    resp = convert_with(pkg, {"O1": 5, "O2": 9, "O3": 7})
    body = resp.json()
    assert body["computed"][0]["population"] == 2
    assert body["computed"][0]["expression"] == "O2 - O3"

    # No old relations exist, and the formula is integral: valid integer
    # inputs always yield an integer new population here.
    resp = convert_with(pkg, {"O1": 5, "O2": 8, "O3": 7})
    assert resp.status_code == 200
    assert resp.json()["computed"][0]["population"] == 1


def test_fractional_coefficients_actually_emitted():
    # A 4x4 incidence structure whose only old relation is O1 - O2 = 0 and
    # whose new district is expressible solely with half-integer coefficients
    # (no integral particular solution exists):
    #   N1(x3) = O1/2 - O3/2 + O4/2.
    old = [
        {"code": "O1", "blocks": ["x3", "x4"]},
        {"code": "O2", "blocks": ["x3", "x4"]},
        {"code": "O3", "blocks": ["x1", "x4"]},
        {"code": "O4", "blocks": ["x1", "x3"]},
    ]
    new = [{"code": "N1", "blocks": ["x3"]}]
    pkg = build(old, new)
    assert pkg["old_relations"] == [{"O1": 1, "O2": -1}]
    n1 = pkg["new_district_relations"][0]
    assert n1["derivable"] is True
    assert n1["coefficients"] == {"O1": "1/2", "O3": "-1/2", "O4": "1/2"}

    # Realizable: x=(1,?,2,3) gives O1=O2=5, O3=4, O4=3 -> N1=2.
    body = convert_with(pkg, {"O1": 5, "O2": 5, "O3": 4, "O4": 3}).json()
    assert body["computed"][0]["population"] == 2
    assert body["computed"][0]["expression"] == (
        "1/2*O1 - 1/2*O3 + 1/2*O4"
    )
    # Half-integer outcome rejects the whole request although all supplied
    # old populations are integral.
    resp = convert_with(pkg, {"O1": 6, "O2": 6, "O3": 4, "O4": 3})
    assert resp.status_code == 400
    assert "non-integer" in resp.json()["detail"]
    # Violating the O1 = O2 coverage relation also rejects everything.
    resp = convert_with(pkg, {"O1": 6, "O2": 7, "O3": 4, "O4": 3})
    assert resp.status_code == 400
    assert "coverage relation" in resp.json()["detail"]
    # With O4 missing, no shift of the formula can avoid an O4 coefficient.
    body = convert_with(pkg, {"O1": 5, "O2": 5, "O3": 4}).json()
    assert body["missing"][0]["code"] == "N1"
    assert body["missing"][0]["missing_reason"] == (
        "insufficient_known_old_districts"
    )


def test_negative_result_rejected():
    # y1 = P1 - P2; populations violating non-negativity of blocks.
    old = [
        {"code": "P1", "blocks": ["y1", "y2"]},
        {"code": "P2", "blocks": ["y2"]},
    ]
    new = [{"code": "M1", "blocks": ["y1"]}]
    pkg = build(old, new)
    resp = convert_with(pkg, {"P1": 3, "P2": 10})
    assert resp.status_code == 400
    assert "negative" in resp.json()["detail"]
    # Missing P2 -> M1 cannot be supported on {P1} alone.
    body = convert_with(pkg, {"P1": 6}).json()
    assert body["missing"][0]["code"] == "M1"
    assert body["missing"][0]["missing_reason"] == (
        "insufficient_known_old_districts"
    )


def test_old_relation_skipped_when_its_districts_have_no_data():
    # Identical coverage => relation O1 - O2 = 0.
    old = [
        {"code": "O1", "blocks": ["x1", "x2"]},
        {"code": "O2", "blocks": ["x1", "x2"]},
        {"code": "O3", "blocks": ["x3"]},
    ]
    new = [{"code": "N3", "blocks": ["x3"]}]
    pkg = build(old, new)
    # Only O3 supplied: the O1/O2 relation is not checkable this time and
    # must be skipped rather than reported.
    resp = convert_with(pkg, {"O3": 11})
    assert resp.status_code == 200
    body = resp.json()
    assert body["checked_relations"] == []
    assert body["computed"][0]["population"] == 11
    # Once both are supplied with contradictory values, it errors.
    resp = convert_with(pkg, {"O1": 4, "O2": 5, "O3": 11})
    assert resp.status_code == 400
    assert "coverage relation" in resp.json()["detail"]
    # With both supplied and consistent, it is checked and satisfied.
    body = convert_with(pkg, {"O1": 4, "O2": 4, "O3": 11}).json()
    assert body["checked_relations"][0]["satisfied"] is True
    assert body["checked_relations"][0]["evaluated_value"] == 0


# --------------------------------------------------------------------------
# 4. Missing data: only currently-known old districts count.
# --------------------------------------------------------------------------
def test_missing_districts_marked():
    old = [
        {"code": "O1", "blocks": ["a"]},
        {"code": "O2", "blocks": ["b"]},
    ]
    new = [
        {"code": "N1", "blocks": ["a", "b"]},
        {"code": "N2", "blocks": ["a"]},
    ]
    pkg = build(old, new)
    resp = convert_with(pkg, {"O1": 4})  # O2 unknown
    body = resp.json()
    assert resp.status_code == 200
    assert [c["code"] for c in body["computed"]] == ["N2"]
    assert body["computed"][0]["population"] == 4
    missing = body["missing"][0]
    assert missing["code"] == "N1"
    assert missing["missing_reason"] == "insufficient_known_old_districts"
    assert body["available_old_districts"] == ["O1"]


def test_subset_derivation_via_relation():
    # Full-rank coverage: O1=x1+x2, O2=x2+x3, O3=x1+x2+x3 (no old relation).
    # N1=x1 = O3 - O2, so it must still compute when O1 is unavailable as
    # long as O2 and O3 have data.
    old = [
        {"code": "O1", "blocks": ["x1", "x2"]},
        {"code": "O2", "blocks": ["x2", "x3"]},
        {"code": "O3", "blocks": ["x1", "x2", "x3"]},
    ]
    new = [{"code": "N1", "blocks": ["x1"]}]
    pkg = build(old, new)
    n1 = pkg["new_district_relations"][0]
    assert n1["derivable"] is True
    # With O1 missing but O2/O3 known: x1 = O3 - O2 must still compute.
    resp = convert_with(pkg, {"O2": 5, "O3": 9})  # x=(4,1,4): O2=5 O3=9
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["computed"][0]["code"] == "N1"
    assert body["computed"][0]["population"] == 4
    # The used formula only mentions known districts.
    assert set(body["computed"][0]["coefficients"]) == {"O2", "O3"}
    # With only O1 known, x1 cannot be isolated -> missing.
    resp = convert_with(pkg, {"O1": 5})
    assert resp.status_code == 200
    assert resp.json()["missing"][0]["code"] == "N1"


# --------------------------------------------------------------------------
# 5. New district not expressible at all from old coverage.
# --------------------------------------------------------------------------
def test_underivable_new_district():
    old = [{"code": "O1", "blocks": ["a"]}]
    new = [{"code": "N1", "blocks": ["b"]}]  # block b never covered by old
    pkg = build(old, new)
    n1 = pkg["new_district_relations"][0]
    assert n1["derivable"] is False
    resp = convert_with(pkg, {"O1": 3})
    body = resp.json()
    assert body["computed"] == []
    assert body["missing"][0]["missing_reason"] == (
        "not_expressible_from_old_districts"
    )


def test_empty_new_district_is_zero():
    old = [{"code": "O1", "blocks": ["a"]}]
    new = [{"code": "N1", "blocks": []}]
    pkg = build(old, new)
    resp = convert_with(pkg, {"O1": 3})
    body = resp.json()
    assert body["computed"][0]["population"] == 0
    assert body["computed"][0]["expression"] == "0"


# --------------------------------------------------------------------------
# 6. Input validation.
# --------------------------------------------------------------------------
def test_input_validation():
    # Duplicate old district code.
    resp = builder.post(
        "/build",
        json={
            "old_districts": [
                {"code": "O1", "blocks": ["a"]},
                {"code": "O1", "blocks": ["b"]},
            ],
            "new_districts": [{"code": "N1", "blocks": ["a"]}],
        },
    )
    assert resp.status_code == 422

    old = [{"code": "O1", "blocks": ["a"]}]
    new = [{"code": "N1", "blocks": ["a"]}]
    pkg = build(old, new)

    # Negative populations are rejected at the schema level.
    resp = convert_with(pkg, {"O1": -1})
    assert resp.status_code == 422

    # Unknown district code.
    resp = convert_with(pkg, {"ZZ": 1})
    assert resp.status_code == 422

    # Tampered package referencing an unknown district.
    bad = dict(pkg)
    bad["old_relations"] = [{"ZZ": 1}]
    resp = convert_with(bad, {"O1": 1})
    assert resp.status_code == 422


def test_block_in_multiple_districts_and_roundtrip():
    old = [
        {"code": "O1", "blocks": ["s1", "s2"]},
        {"code": "O2", "blocks": ["s2", "s3"]},
    ]
    new = [
        {"code": "N1", "blocks": ["s1"]},
        {"code": "N2", "blocks": ["s2"]},
        {"code": "N3", "blocks": ["s3"]},
    ]
    pkg = build(old, new)
    # Package contains only codes and rational relations.
    assert set(pkg) == {
        "schema_version",
        "old_districts",
        "new_districts",
        "old_relations",
        "new_district_relations",
    }
    # Ground truth block populations: s1=2, s2=3, s3=5 -> O1=5, O2=8.
    # N1 = O1 - s2 is not directly ... N2=O1+O2-N3 etc; check what derives:
    resp = convert_with(pkg, {"O1": 5, "O2": 8})
    body = resp.json()
    # N1+N2 = O1 and N2+N3 = O2 with N1+N2+N3 not given; individual small
    # districts are underdetermined -> all missing, no crash.
    assert body["computed"] == []
    assert {m["code"] for m in body["missing"]} == {"N1", "N2", "N3"}

    # But N1+N2 as a new district covering s1,s2 equals O1 exactly.
    new2 = [{"code": "N12", "blocks": ["s1", "s2"]}]
    pkg2 = build(old, new2)
    resp = convert_with(pkg2, {"O1": 5, "O2": 8})
    assert resp.json()["computed"][0]["population"] == 5
