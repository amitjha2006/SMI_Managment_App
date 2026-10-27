"""Per-product production workflow.

Spec 6 asks for a configurable workflow where "a product may skip stages", and
spec 19 forbids hard-coding production stages. The ProductionStage enum remains
the vocabulary of possible stages; what is configurable is which of them a
product uses, and in what order.

Why a missing row means "use everything"
---------------------------------------
Workflows are stored only once someone changes them. A product with no rows is
on the full default sequence. That is what makes this feature safe to add to a
live system: every existing product keeps working with no data migration, and
no report that reads stage_entries can be surprised by a product quietly
switching to one stage.

Rules enforced
--------------
- at least one stage (a product with no stages cannot be produced)
- no duplicates
- only known stage names, matched case-insensitively
- the caller's order is preserved, so mixing before grinding is expressible

Pure validation helpers are separated from the database access so the rules can
be unit tested without one.
"""

from backend.app.models.production import ProductionStage, ProductWorkflowStage

# The full base workflow from the spec: Raw Material -> Roasting -> Grinding ->
# Mixing -> Packaging. Used whenever a product has no explicit configuration.
DEFAULT_STAGES = tuple(s.value for s in ProductionStage)

ALL_STAGES = tuple(s.value for s in ProductionStage)

_STAGE_LOOKUP = {}
for _s in ProductionStage:
    _STAGE_LOOKUP[_s.value.lower()] = _s.value
    _STAGE_LOOKUP[_s.name.lower()] = _s.value


class WorkflowError(ValueError):
    """Raised for an invalid workflow. Callers turn this into a 400."""


def normalise_stage(name) -> str:
    """Canonical lowercase value for a stage name, or raise."""
    key = str(name or "").strip().lower()
    if key not in _STAGE_LOOKUP:
        raise WorkflowError(f"Unknown stage '{name}'. Valid stages: {', '.join(ALL_STAGES)}")
    return _STAGE_LOOKUP[key]


def validate_stages(stages) -> list:
    """Validate and canonicalise a submitted list of stages.

    Returns the list in the caller's order. Raises WorkflowError with a message
    meant to be shown to the user as-is.
    """
    if stages is None or isinstance(stages, (str, bytes)):
        raise WorkflowError('Stages must be a list, for example ["grinding", "packaging"]')
    stages = list(stages)
    if not stages:
        raise WorkflowError(
            "A product needs at least one stage. Keep at least one, or the "
            "product cannot be produced."
        )
    out, seen = [], set()
    for raw in stages:
        s = normalise_stage(raw)
        if s in seen:
            raise WorkflowError(f"Stage '{s}' is listed twice.")
        seen.add(s)
        out.append(s)
    return out


def default_workflow() -> list:
    return list(DEFAULT_STAGES)


def workflow_rows(db, product_id) -> list:
    """Configured stages for a product in order, or [] when unconfigured."""
    rows = (
        db.query(ProductWorkflowStage)
        .filter(ProductWorkflowStage.product_id == product_id)
        .order_by(ProductWorkflowStage.sequence, ProductWorkflowStage.id)
        .all()
    )
    return [r.stage.value for r in rows]


def workflow_for(db, product_id) -> list:
    """The stages a production entry for this product should follow."""
    rows = workflow_rows(db, product_id)
    return rows or default_workflow()


def is_default(db, product_id) -> bool:
    return not workflow_rows(db, product_id)


def set_workflow(db, product_id, stages) -> list:
    """Replace a product's workflow. Returns the stored, canonical list.

    Idempotent: setting the default sequence clears the rows entirely rather
    than storing four rows that merely match the default, so "unconfigured"
    stays a single, honest state.
    """
    wanted = validate_stages(stages)

    if wanted == default_workflow():
        db.query(ProductWorkflowStage).filter(ProductWorkflowStage.product_id == product_id).delete(
            synchronize_session=False
        )
        return wanted

    db.query(ProductWorkflowStage).filter(ProductWorkflowStage.product_id == product_id).delete(
        synchronize_session=False
    )
    for i, name in enumerate(wanted, 1):
        db.add(
            ProductWorkflowStage(
                product_id=product_id, stage=_STAGE_LOOKUP[name.lower()], sequence=i
            )
        )
    return wanted


def check_stages_allowed(db, product_id, submitted) -> None:
    """Reject stage entries that are not part of this product's workflow.

    Without this the workflow would be decorative: a blended-only product could
    still be recorded as roasted, and per-stage loss reports would mix two
    different processes together.
    """
    allowed = workflow_for(db, product_id)
    if not submitted:
        return
    for raw in submitted:
        name = normalise_stage(raw)
        if name not in allowed:
            raise WorkflowError(
                f"Stage '{name}' is not part of this product's workflow. "
                f"This product uses: {', '.join(allowed)}. "
                f"Change the product's workflow first if that is wrong."
            )
