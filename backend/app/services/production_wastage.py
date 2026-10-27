"""Production stage wastage: reusable calculation rules.

Wastage is the material lost at a processing stage. Both figures are derived
from the same two recorded numbers - stage input and stage output - so they can
never disagree with each other:

    wastage_kg  = max(0, input_kg - output_kg)
    wastage_pct = wastage_kg / input_kg * 100      (0 when input is 0)

    Exception: a PACKAGING stage may report more output than input because
    the wrapper adds real weight. That surplus is stored separately as
    packing_gain_kg (pass is_packaging=True) - still with zero wastage, and
    never netted against loss in any total.

Deriving this server-side rather than trusting a submitted figure is the point.
The percentage is what gets reported, so a request that supplies only one half
of the pair must not be able to invent loss the recorded inputs do not support.

On the denominator: wastage is reported against TOTAL STAGE INPUT, never
against final production output. Summing input-minus-output across every stage
measures loss relative to what was actually put in; dividing by final output
mixes two different quantities and overstates the rate badly in a multi-stage
workflow. For the reference dataset that mistake reads 15.35% instead of the
honest figure.

Pure functions only - no database, no Flask/FastAPI imports - so the rules can
be unit tested directly, the same way stock_analysis.py is.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# Numeric precisions: quantity (15,3), percentage (5,2)  # noqa: ERA001 - schema doc
QTY_EXP = Decimal("0.001")
PCT_EXP = Decimal("0.01")


def _dec(value) -> Decimal:
    """Tolerant Decimal conversion - never raises on None/blank/garbage."""
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _q(value: Decimal, exp: Decimal) -> Decimal:
    return _dec(value).quantize(exp, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class StageLoss:
    """Loss at one processing stage."""

    input_kg: Decimal
    output_kg: Decimal
    wastage_kg: Decimal
    wastage_pct: Decimal
    # Packing gain (kg): output above input on a PACKAGING stage, where the
    # wrapper adds real weight. Kept apart from wastage so a gain can never
    # cancel out true loss when stages are totalled. Always 0 unless
    # is_packaging is passed.
    packing_gain_kg: Decimal

    def as_dict(self) -> dict:
        return {
            "input_kg": self.input_kg,
            "output_kg": self.output_kg,
            "wastage_kg": self.wastage_kg,
            "wastage_pct": self.wastage_pct,
            "packing_gain_kg": self.packing_gain_kg,
        }


def stage_loss(input_kg, output_kg, is_packaging=False) -> StageLoss:
    """Loss for a single stage.

    Clamps at zero: a stage that reports more output than input is a data
    mistake, not negative wastage, so the loss is reported as 0 rather than
    subtracting from totals. `wastage_pct` is 0 when there was no input, since
    a rate against zero is undefined rather than infinite.

    The one exception is a PACKAGING stage (is_packaging=True): packed output
    legitimately weighs more than the loose input because the wrapper adds
    weight. That surplus is reported as packing_gain_kg - still with zero
    wastage, and never netted against loss anywhere downstream.
    """
    in_kg = _dec(input_kg)
    out_kg = _dec(output_kg)
    loss = in_kg - out_kg
    gain = Decimal("0")
    if loss < 0:
        if is_packaging:
            gain = -loss
        loss = Decimal("0")
    pct = Decimal("0")
    if in_kg > 0:
        pct = (loss / in_kg * Decimal("100")).quantize(PCT_EXP, rounding=ROUND_HALF_UP)
    return StageLoss(
        input_kg=_q(in_kg, QTY_EXP),
        output_kg=_q(out_kg, QTY_EXP),
        wastage_kg=_q(loss, QTY_EXP),
        wastage_pct=pct,
        packing_gain_kg=_q(gain, QTY_EXP),
    )


@dataclass(frozen=True)
class CategoryWastage:
    """Wastage rolled up for one category."""

    stage_input_kg: Decimal
    stage_output_kg: Decimal
    wastage_kg: Decimal
    wastage_pct: Decimal

    def as_dict(self) -> dict:
        return {
            "stage_input_kg": self.stage_input_kg,
            "stage_output_kg": self.stage_output_kg,
            "wastage_kg": self.wastage_kg,
            "wastage_pct": self.wastage_pct,
        }


def aggregate_by_category(
    rows: Iterable[tuple],
) -> dict[object, CategoryWastage]:
    """Roll stage losses up per category.

    `rows` yields (category_key, input_kg, output_kg).

    Each row goes through stage_loss() before being summed, so a stage that
    reports more output than input contributes zero rather than a negative.
    Summing the raw inputs and outputs first and subtracting once at the end
    would let a single backwards stage cancel out real loss recorded on the
    other stages of the same category. Clamping is a per-stage decision, and
    this also keeps the total identical to the sum of the values the production
    save path actually stored.

    The wastage total is recomputed from input/output rather than read from a
    stored column, so historical rows that predate wastage_quantity still
    aggregate correctly.
    """
    totals: dict[object, list] = {}
    for row in rows:
        try:
            key, raw_in, raw_out = row[0], row[1], row[2]
        except (TypeError, IndexError):
            continue
        loss = stage_loss(raw_in, raw_out)
        bucket = totals.setdefault(key, [Decimal("0"), Decimal("0"), Decimal("0")])
        bucket[0] += loss.input_kg
        bucket[1] += loss.output_kg
        bucket[2] += loss.wastage_kg

    out: dict[object, CategoryWastage] = {}
    for key, (in_kg, out_kg, loss_kg) in totals.items():
        pct = Decimal("0")
        if in_kg > 0:
            pct = (loss_kg / in_kg * Decimal("100")).quantize(PCT_EXP, rounding=ROUND_HALF_UP)
        out[key] = CategoryWastage(
            stage_input_kg=_q(in_kg, QTY_EXP),
            stage_output_kg=_q(out_kg, QTY_EXP),
            wastage_kg=_q(loss_kg, QTY_EXP),
            wastage_pct=pct,
        )
    return out
