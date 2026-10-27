"""Tests for the per-product production workflow.

Spec 6: "Workflow is configurable per product; a product may skip stages."
Spec 19: "Do not hard-code production stages."

The rule that makes this safe to ship is the fallback: a product with no saved
workflow is on the full default sequence. Every existing product therefore
keeps working with no data migration, and "unconfigured" stays one honest
state rather than four rows that merely happen to match the default.

Runs against an in-memory SQLite database, never the real one.
Pure rules are separated from database access so most of this needs no DB.

Run: python backend/tests/test_production_workflow.py
"""

import os
import sys

# Runnable directly (`python backend/tests/test_production_workflow.py`) as well
# as under pytest. Under pytest the conftest fixtures below are injected.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app import models  # noqa: E402
from backend.app.services import production_workflow as wf  # noqa: E402

_seq = iter(range(1, 10000))


def _product(db, name="Test Masala", seq=None):
    """A product row.

    IDs come from a module-level counter by default so two products in one
    database never collide on the unique product_id. Pass `seq` to control it
    explicitly; omitting the counter here silently reintroduces the collision.
    """
    n = next(seq) if seq is not None else next(_seq)
    p = models.Product(product_id=f"P-{n:04d}", name=name)
    db.add(p)
    db.commit()
    return p


# --- pure rules ------------------------------------------------------------


def test_default_is_the_full_base_workflow():
    # Spec: Raw Material -> Roasting -> Grinding -> Mixing -> Packaging
    assert wf.default_workflow() == ["roasting", "grinding", "mixing", "packaging"]


def test_stage_names_are_matched_case_insensitively():
    assert wf.normalise_stage("ROASTING") == "roasting"
    assert wf.normalise_stage(" Roasting ") == "roasting"
    assert wf.validate_stages(["Grinding", "PACKAGING"]) == ["grinding", "packaging"]


def test_unknown_stage_is_rejected():
    for bad in ("frying", "roasitng", "", None, 7):
        try:
            wf.normalise_stage(bad)
        except wf.WorkflowError:
            continue
        raise AssertionError(f"{bad!r} should have been rejected")


def test_a_product_may_skip_stages():
    # The whole point of the feature.
    assert wf.validate_stages(["mixing", "packaging"]) == ["mixing", "packaging"]
    assert wf.validate_stages(["grinding"]) == ["grinding"]


def test_empty_workflow_is_rejected():
    try:
        wf.validate_stages([])
    except wf.WorkflowError as e:
        assert "at least one stage" in str(e).lower()
    else:
        raise AssertionError("an empty list should be rejected")


def test_missing_workflow_is_rejected_as_not_a_list():
    # None / "" mean "no list was sent", which is a different mistake from
    # sending an empty list, so the message differs - but both are refused.
    for bad in (None, ""):
        try:
            wf.validate_stages(bad)
        except wf.WorkflowError as e:
            assert "list" in str(e).lower()
        else:
            raise AssertionError(f"{bad!r} should be rejected")


def test_duplicate_stages_are_rejected():
    try:
        wf.validate_stages(["grinding", "grinding"])
    except wf.WorkflowError as e:
        assert "twice" in str(e).lower()
        return
    raise AssertionError("duplicate stages should be rejected")


def test_a_bare_string_is_rejected():
    # "grinding" is a string, not a list of one stage.
    try:
        wf.validate_stages("grinding")
    except wf.WorkflowError:
        return
    raise AssertionError("a bare string should be rejected")


def test_order_is_preserved():
    # Mixing before grinding is unusual but is the admin's call.
    assert wf.validate_stages(["packaging", "grinding"]) == ["packaging", "grinding"]


# --- database-backed behaviour -------------------------------------------


def test_unconfigured_product_gets_the_default(db):
    p = _product(db)
    assert wf.workflow_for(db, p.id) == ["roasting", "grinding", "mixing", "packaging"]
    assert wf.is_default(db, p.id) is True
    assert wf.workflow_rows(db, p.id) == []


def test_setting_a_custom_workflow_persists_in_order(db):
    p = _product(db)
    got = wf.set_workflow(db, p.id, ["grinding", "packaging"])
    db.commit()
    assert got == ["grinding", "packaging"]
    assert wf.workflow_for(db, p.id) == ["grinding", "packaging"]
    assert wf.is_default(db, p.id) is False
    rows = (
        db.query(models.ProductWorkflowStage)
        .filter(models.ProductWorkflowStage.product_id == p.id)
        .all()
    )
    assert [r.sequence for r in rows] == [1, 2]
    assert [r.stage.value for r in rows] == ["grinding", "packaging"]


def test_setting_the_default_clears_the_rows(db):
    # Storing four rows that match the default would leave "unconfigured"
    # unrepresentable. It should go back to being unconfigured.
    p = _product(db)
    wf.set_workflow(db, p.id, ["grinding", "packaging"])
    db.commit()
    assert wf.is_default(db, p.id) is False
    wf.set_workflow(db, p.id, wf.default_workflow())
    db.commit()
    assert wf.workflow_rows(db, p.id) == []
    assert wf.is_default(db, p.id) is True
    assert wf.workflow_for(db, p.id) == wf.default_workflow()


def test_workflow_can_be_replaced_wholesale(db):
    p = _product(db)
    wf.set_workflow(db, p.id, ["roasting", "grinding", "mixing", "packaging"])
    db.commit()
    wf.set_workflow(db, p.id, ["mixing", "packaging"])
    db.commit()
    assert wf.workflow_for(db, p.id) == ["mixing", "packaging"]
    assert (
        db.query(models.ProductWorkflowStage)
        .filter(models.ProductWorkflowStage.product_id == p.id)
        .count()
        == 2
    )


def test_workflows_are_per_product(db):
    a = _product(db, "Blended Only")
    b = _product(db, "Full Process")
    wf.set_workflow(db, a.id, ["mixing", "packaging"])
    wf.set_workflow(db, b.id, wf.default_workflow())
    db.commit()
    assert wf.workflow_for(db, a.id) == ["mixing", "packaging"]
    assert wf.workflow_for(db, b.id) == wf.default_workflow()


def test_stages_outside_the_workflow_are_refused(db):
    p = _product(db)
    wf.set_workflow(db, p.id, ["mixing", "packaging"])
    db.commit()
    # allowed
    wf.check_stages_allowed(db, p.id, ["mixing", "packaging"])
    # refused, and the message says what IS allowed
    try:
        wf.check_stages_allowed(db, p.id, ["roasting", "mixing"])
    except wf.WorkflowError as e:
        assert "roasting" in str(e)
        assert "mixing, packaging" in str(e)
        return
    raise AssertionError("a stage outside the workflow should be refused")


def test_unconfigured_product_still_accepts_every_stage(db):
    # Backwards compatibility: with no workflow saved, the old behaviour of
    # accepting any valid stage must still hold, or existing usage breaks.
    p = _product(db)
    wf.check_stages_allowed(db, p.id, ["roasting", "grinding", "mixing", "packaging"])
    wf.check_stages_allowed(db, p.id, [])


def test_deleting_a_product_removes_its_workflow(db):
    """ORM-level delete cascades via the relationship.

    Worth being precise about: SQLite does not enforce foreign keys here (the
    app never sets PRAGMA foreign_keys=ON), so the ondelete="CASCADE" on the
    column is inert at the database level. The cascade that actually does
    something is the ORM relationship's delete-orphan, which only fires on
    db.delete(parent) - not on a bulk query.delete().

    In practice this is not a live risk: the app soft-deletes products
    (is_active = False) and never hard-deletes them, so a product row always
    survives to own its workflow.
    """
    p = _product(db)
    pid = p.id
    wf.set_workflow(db, pid, ["grinding", "packaging"])
    db.commit()
    assert (
        db.query(models.ProductWorkflowStage)
        .filter(models.ProductWorkflowStage.product_id == pid)
        .count()
        == 2
    )

    db.delete(p)  # ORM path: relationship cascade applies
    db.commit()
    assert (
        db.query(models.ProductWorkflowStage)
        .filter(models.ProductWorkflowStage.product_id == pid)
        .count()
        == 0
    )


def test_soft_deleting_a_product_keeps_its_workflow(db):
    # The path the app actually uses: the product row survives, so the workflow
    # it owns must survive with it.
    p = _product(db)
    pid = p.id
    wf.set_workflow(db, pid, ["mixing", "packaging"])
    db.commit()
    db.query(models.Product).filter(models.Product.id == pid).update({"is_active": False})
    db.commit()
    assert wf.workflow_for(db, pid) == ["mixing", "packaging"]


if __name__ == "__main__":
    # Delegate to pytest. Hand-calling the test functions cannot supply the
    # fixtures from conftest, so a direct run of a database test would fail
    # while the suite passed - two different answers to the same question.
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
