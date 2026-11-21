"""End-to-end tests for the county-boundary analysis package (schema v2)."""
import json

from fastapi.testclient import TestClient

from app.builder_app import app as builder_app
from app.converter_app import app as converter_app

builder = TestClient(builder_app)
converter = TestClient(converter_app)


def build(old, new, before, after, **extra):
    payload = {
        "old_districts": old,
        "new_districts": new,
        "before_districts": before,
        "after_districts": after,
    }
    payload.update(extra)
    resp = builder.post("/analysis/build", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def analyze(package, old_populations=None, new_populations=None, **extra):
    payload = {
        "package": package,
        "old_populations": old_populations or {},
        "new_populations": new_populations or {},
    }
    payload.update(extra)
    return converter.post("/analysis/convert", json=payload)


def values(body):
    return {
        "total_change": body["total_change"]["difference"],
        "boundary_effect": body["boundary_effect"]["difference"],
        "same_coverage_change": body["same_coverage_change"]["difference"],
    }


# --------------------------------------------------------------------------
# 1. Basic decomposition, with blocks deduplicated inside each group.
# --------------------------------------------------------------------------
def test_three_differences_and_group_block_dedup():
    old = [
        {"code": "O1", "blocks": ["a", "b"]},
        {"code": "O2", "blocks": ["c"]},
        {"code": "O3", "blocks": ["b"]},  # nested duplicate of O1's block
    ]
    new = [
        {"code": "N1", "blocks": ["a"]},
        {"code": "N2", "blocks": ["b"]},
        {"code": "N3", "blocks": ["c"]},
    ]
    pkg = build(old, new, ["O1", "O3"], ["N1", "N2", "N3"])
    assert pkg["schema_version"] == 2
    # Before group {a, b}; O3 adds nothing new, so the boundary is O1 alone.
    assert pkg["old_boundary"]["derivable"] is True
    assert pkg["old_boundary"]["coefficients"] == {"O1": "1"}
    # Baseline new boundary {a, b, c} = O1 + O2 in old-population space.
    assert pkg["new_boundary_baseline"]["coefficients"] == {"O1": "1", "O2": "1"}
    # Report new boundary {a, b, c} = N1 + N2 + N3.
    assert pkg["new_boundary_report"]["coefficients"] == {
        "N1": "1",
        "N2": "1",
        "N3": "1",
    }
    assert pkg["boundary_effect"]["coefficients"] == {"O2": "1"}

    # Ground truth a=5, b=2, c=3 -> O1=7, O2=3, O3=2; N=5,2,3.
    resp = analyze(
        pkg,
        {"O1": 7, "O2": 3, "O3": 2},
        {"N1": 5, "N2": 2, "N3": 3},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert values(body) == {
        "total_change": 3,  # 10 - 7
        "boundary_effect": 3,  # 10 - 7
        "same_coverage_change": 0,  # 10 - 10
    }
    assert body["differences"] == {
        "total_change": 3,
        "boundary_effect": 3,
        "same_coverage_change": 0,
    }
    assert body["missing"] == []
    for item in body["changes"]:
        assert item["status"] == "computed"
        assert item["formula"] is not None
        assert item["expression"] is not None
    assert "(N1 + N2 + N3) - (O1)" in body["total_change"]["formula"]


def test_new_side_block_dedup():
    old = [
        {"code": "O1", "blocks": ["a"]},
        {"code": "O2", "blocks": ["b"]},
        {"code": "O3", "blocks": ["c"]},
    ]
    new = [
        {"code": "N1", "blocks": ["a", "b"]},
        {"code": "N2", "blocks": ["b"]},  # duplicate coverage inside group
        {"code": "N3", "blocks": ["c"]},
    ]
    pkg = build(old, new, ["O1", "O2"], ["N1", "N2", "N3"])
    # Union is {a, b, c}: N2 contributes nothing because N1 already has b.
    assert pkg["new_boundary_report"]["coefficients"] == {"N1": "1", "N3": "1"}
    body = analyze(
        pkg,
        {"O1": 5, "O2": 2, "O3": 3},
        {"N1": 7, "N2": 2, "N3": 3},
    ).json()
    assert body["baseline"]["new_boundary"]["population"] == 10
    assert body["report"]["new_boundary"]["population"] == 10
    assert values(body)["total_change"] == 3


# --------------------------------------------------------------------------
# 2. Boundary effect directly derivable while both boundary levels are not.
# --------------------------------------------------------------------------
def test_boundary_effect_derivable_when_both_levels_missing():
    old = [
        {"code": "O1", "blocks": ["a"]},
        {"code": "O2", "blocks": ["b"]},
        {"code": "O3", "blocks": ["c"]},
    ]
    new = [
        {"code": "N1", "blocks": ["a"]},
        {"code": "N2", "blocks": ["b"]},
        {"code": "N3", "blocks": ["c"]},
    ]
    # Baseline old boundary {a, c}; baseline/report new boundary {b, c}.
    # Only O1, O2 and N2, N3 are reported: each boundary needs O3, yet the
    # boundary effect O2 - O1 is still exactly derivable.
    pkg = build(old, new, ["O1", "O3"], ["N2", "N3"])
    assert pkg["boundary_effect"]["coefficients"] == {"O1": "-1", "O2": "1"}

    resp = analyze(pkg, {"O1": 5, "O2": 8}, {"N2": 8, "N3": 4})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["baseline"]["old_boundary"]["status"] == "missing"
    assert body["baseline"]["new_boundary"]["status"] == "missing"
    assert body["boundary_effect"]["status"] == "computed"
    assert body["boundary_effect"]["difference"] == 3
    assert body["boundary_effect"]["expression"] == "-O1 + O2"
    assert body["total_change"]["status"] == "missing"
    assert body["same_coverage_change"]["status"] == "missing"
    assert body["total_change"]["missing_reason"] == (
        "insufficient_known_old_districts"
    )
    assert body["missing"] == [
        {
            "code": "total_change",
            "missing_reason": "insufficient_known_old_districts",
        },
        {
            "code": "same_coverage_change",
            "missing_reason": "insufficient_known_old_districts",
        },
    ]


# --------------------------------------------------------------------------
# 3. Independent determination: total computable while others are not.
# --------------------------------------------------------------------------
def test_differences_are_judged_independently():
    # New boundary reaches a block ("d") never covered by an old district.
    old = [
        {"code": "O1", "blocks": ["a"]},
        {"code": "O2", "blocks": ["b"]},
    ]
    new = [
        {"code": "N1", "blocks": ["a"]},
        {"code": "N2", "blocks": ["d"]},
    ]
    pkg = build(old, new, ["O1"], ["N1", "N2"])
    assert pkg["new_boundary_baseline"]["derivable"] is False
    body = analyze(
        pkg, {"O1": 4, "O2": 6}, {"N1": 4, "N2": 7}
    ).json()
    assert values(body)["total_change"] == 7  # report 11 - baseline old 4
    assert body["same_coverage_change"]["status"] == "missing"
    assert body["same_coverage_change"]["missing_reason"] == (
        "not_expressible_from_old_districts"
    )
    assert body["boundary_effect"]["status"] == "missing"


def test_missing_new_district_data():
    old = [
        {"code": "O1", "blocks": ["a"]},
        {"code": "O2", "blocks": ["b"]},
    ]
    new = [
        {"code": "N1", "blocks": ["a"]},
        {"code": "N2", "blocks": ["b"]},
    ]
    pkg = build(old, new, ["O1", "O2"], ["N1", "N2"])
    body = analyze(pkg, {"O1": 3, "O2": 4}, {"N1": 3}).json()
    assert body["report"]["new_boundary"]["status"] == "missing"
    assert body["report"]["new_boundary"]["missing_reason"] == (
        "insufficient_known_new_districts"
    )
    assert values(body)["boundary_effect"] == 0  # both baseline levels = O1+O2
    assert body["total_change"]["status"] == "missing"
    assert body["same_coverage_change"]["status"] == "missing"


# --------------------------------------------------------------------------
# 4. Fractions and negative differences.
# --------------------------------------------------------------------------
def _fractional_package():
    # O1 = a+b, O2 = b+c, O3 = a+c: the boundaries need halves, but no
    # coverage relation exists between the old districts.
    old = [
        {"code": "O1", "blocks": ["a", "b"]},
        {"code": "O2", "blocks": ["b", "c"]},
        {"code": "O3", "blocks": ["a", "c"]},
    ]
    new = [{"code": "N1", "blocks": ["a", "b"]}]
    return build(old, new, ["O1", "O2"], ["N1"])


def test_fractional_formula_and_negative_difference():
    pkg = _fractional_package()
    assert pkg["old_boundary"]["coefficients"] == {
        "O1": "1/2",
        "O2": "1/2",
        "O3": "1/2",
    }
    # O = (2, 2, 2): every boundary level is integral (3) and the signed
    # differences may be negative.
    resp = analyze(pkg, {"O1": 2, "O2": 2, "O3": 2}, {"N1": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert values(body) == {
        "total_change": -1,  # N1(2) - baseline old (3)
        "boundary_effect": -1,  # baseline new (2) - baseline old (3)
        "same_coverage_change": 0,  # 2 - 2
    }
    assert "1/2*O1" in body["total_change"]["formula"]


def test_non_integer_difference_rejects_whole_request():
    pkg = _fractional_package()
    # O = (1, 1, 1): baseline old = 3/2, so total change = 1 - 3/2 = -1/2.
    resp = analyze(pkg, {"O1": 1, "O2": 1, "O3": 1}, {"N1": 1})
    assert resp.status_code == 400
    assert "non-integer" in resp.json()["detail"] or (
        "integer" in resp.json()["detail"]
    )
    assert set(resp.json()) == {"error", "detail"}


# --------------------------------------------------------------------------
# 5. Coverage-relation contradictions fail the whole request, per period.
# --------------------------------------------------------------------------
def test_old_period_relation_contradiction():
    old = [
        {"code": "O1", "blocks": ["x"]},
        {"code": "O2", "blocks": ["x"]},  # O1 - O2 = 0
        {"code": "O3", "blocks": ["y"]},
    ]
    new = [
        {"code": "N1", "blocks": ["x"]},
        {"code": "N2", "blocks": ["y"]},
    ]
    pkg = build(old, new, ["O3"], ["N1", "N2"])
    assert pkg["old_relations"] == [{"O1": 1, "O2": -1}]

    ok = analyze(pkg, {"O1": 5, "O2": 5, "O3": 2}, {"N1": 5, "N2": 2})
    assert ok.status_code == 200, ok.text
    assert ok.json()["checked_old_relations"][0]["satisfied"] is True
    assert ok.json()["checked_new_relations"] == []

    bad = analyze(pkg, {"O1": 5, "O2": 6, "O3": 2}, {"N1": 5, "N2": 2})
    assert bad.status_code == 400
    assert "coverage relation" in bad.json()["detail"]
    assert "old" in bad.json()["detail"]

    # The relation is not checkable when one side has no data -> skipped.
    skipped = analyze(pkg, {"O1": 5, "O3": 2}, {"N1": 5, "N2": 2})
    assert skipped.status_code == 200
    assert skipped.json()["checked_old_relations"] == []


def test_new_period_relation_contradiction():
    old = [
        {"code": "O1", "blocks": ["x"]},
        {"code": "O2", "blocks": ["y"]},
    ]
    new = [
        {"code": "N1", "blocks": ["x"]},
        {"code": "N2", "blocks": ["x"]},  # N1 - N2 = 0
        {"code": "N3", "blocks": ["y"]},
    ]
    pkg = build(old, new, ["O1", "O2"], ["N1", "N2", "N3"])
    assert pkg["new_relations"] == [{"N1": 1, "N2": -1}]

    bad = analyze(
        pkg,
        {"O1": 4, "O2": 7},
        {"N1": 4, "N2": 5, "N3": 6},
    )
    assert bad.status_code == 400
    assert "new" in bad.json()["detail"]
    assert "coverage relation" in bad.json()["detail"]

    ok = analyze(
        pkg,
        {"O1": 4, "O2": 7},
        {"N1": 4, "N2": 4, "N3": 6},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["checked_new_relations"][0]["satisfied"] is True


# --------------------------------------------------------------------------
# 6. Package hygiene and input validation.
# --------------------------------------------------------------------------
def test_package_contains_no_blocks():
    old = [
        {"code": "O1", "blocks": ["blk-a", "blk-b"]},
        {"code": "O2", "blocks": ["blk-c"]},
    ]
    new = [
        {"code": "N1", "blocks": ["blk-a"]},
        {"code": "N2", "blocks": ["blk-c"]},
    ]
    pkg = build(old, new, ["O1", "O2"], ["N1", "N2"])
    text = json.dumps(pkg)
    assert "blk-" not in text
    assert set(pkg) == {
        "schema_version",
        "kind",
        "old_districts",
        "new_districts",
        "old_relations",
        "new_relations",
        "old_boundary",
        "new_boundary_baseline",
        "new_boundary_report",
        "boundary_effect",
    }


def test_build_validation():
    old = [{"code": "O1", "blocks": ["a"]}]
    new = [{"code": "N1", "blocks": ["a"]}]

    # A before group must consist of old codes.
    resp = builder.post(
        "/analysis/build",
        json={
            "old_districts": old,
            "new_districts": new,
            "before_districts": ["N1"],
            "after_districts": ["N1"],
        },
    )
    assert resp.status_code == 422

    # An after group must consist of new codes.
    resp = builder.post(
        "/analysis/build",
        json={
            "old_districts": old,
            "new_districts": new,
            "before_districts": ["O1"],
            "after_districts": ["O1"],
        },
    )
    assert resp.status_code == 422

    # Duplicate district codes are rejected as in v1.
    resp = builder.post(
        "/analysis/build",
        json={
            "old_districts": old + old,
            "new_districts": new,
            "before_districts": ["O1"],
            "after_districts": ["N1"],
        },
    )
    assert resp.status_code == 422

    # The full block lists are still required.
    resp = builder.post(
        "/analysis/build",
        json={
            "old_districts": [{"code": "O1"}],
            "new_districts": new,
            "before_districts": ["O1"],
            "after_districts": ["N1"],
        },
    )
    assert resp.status_code == 422


def test_convert_validation():
    old = [{"code": "O1", "blocks": ["a"]}]
    new = [{"code": "N1", "blocks": ["a"]}]
    pkg = build(old, new, ["O1"], ["N1"])

    assert analyze(pkg, {"O1": -1}, {"N1": 1}).status_code == 422
    assert analyze(pkg, {"O1": 1}, {"N1": -2}).status_code == 422
    assert analyze(pkg, {"ZZ": 1}, {"N1": 1}).status_code == 422
    assert analyze(pkg, {"O1": 1}, {"ZZ": 1}).status_code == 422
    assert analyze(pkg, {"O1": 1.5}, {"N1": 1}).status_code == 422

    tampered = json.loads(json.dumps(pkg))
    tampered["old_boundary"]["coefficients"] = {"ZZ": "1"}
    assert analyze(tampered, {"O1": 1}, {"N1": 1}).status_code == 422

    tampered = json.loads(json.dumps(pkg))
    tampered["old_relations"] = [{"ZZ": 1}]
    assert analyze(tampered, {"O1": 1}, {"N1": 1}).status_code == 422


def test_combined_populations_map():
    old = [
        {"code": "O1", "blocks": ["a"]},
        {"code": "O2", "blocks": ["b"]},
    ]
    new = [
        {"code": "N1", "blocks": ["a"]},
        {"code": "N2", "blocks": ["b"]},
    ]
    pkg = build(old, new, ["O1"], ["N1"])
    resp = converter.post(
        "/analysis/convert",
        json={
            "package": pkg,
            "populations": {"O1": 3, "O2": 9, "N1": 4, "N2": 9},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available_old_districts"] == ["O1", "O2"]
    assert body["available_new_districts"] == ["N1", "N2"]
    assert values(body)["total_change"] == 1


# --------------------------------------------------------------------------
# 7. Existing v1 behaviour and docs are untouched.
# --------------------------------------------------------------------------
def test_v1_endpoints_still_available():
    resp = builder.post(
        "/build",
        json={
            "old_districts": [{"code": "O1", "blocks": ["a"]}],
            "new_districts": [{"code": "N1", "blocks": ["a"]}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["schema_version"] == 1

    resp = converter.post(
        "/convert",
        json={"package": resp.json(), "old_populations": {"O1": 3}},
    )
    assert resp.status_code == 200
    assert resp.json()["computed"][0]["population"] == 3

    paths = builder.get("/openapi.json").json()["paths"]
    assert "/build" in paths and "/analysis/build" in paths
    paths = converter.get("/openapi.json").json()["paths"]
    assert "/convert" in paths and "/analysis/convert" in paths


def test_empty_old_boundary_group_is_zero():
    old = [{"code": "O1", "blocks": ["a"]}]
    new = [{"code": "N1", "blocks": ["a"]}]
    pkg = build(old, new, [], ["N1"])
    assert pkg["old_boundary"]["coefficients"] == {}
    assert pkg["old_boundary"]["derivable"] is True
    assert pkg["new_boundary_report"]["coefficients"] == {"N1": "1"}
    body = analyze(pkg, {"O1": 3}, {"N1": 5}).json()
    # Baseline old boundary is empty (0), baseline new boundary is O1 = 3,
    # report new boundary is N1 = 5.
    assert values(body) == {
        "total_change": 5,
        "boundary_effect": 3,
        "same_coverage_change": 2,
    }


def test_empty_boundary_groups_are_zero():
    old = [{"code": "O1", "blocks": ["a"]}]
    new = [{"code": "N1", "blocks": ["a"]}]
    pkg = build(old, new, [], [])
    body = analyze(pkg, {"O1": 3}, {"N1": 5}).json()
    assert values(body) == {
        "total_change": 0,
        "boundary_effect": 0,
        "same_coverage_change": 0,
    }
