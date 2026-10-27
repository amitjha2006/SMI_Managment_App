"""Product Stock & Sales Analysis — reusable calculation engine.

Source-of-truth rules (confirmed against the reference report):
  BAL (packets) = Stock (packets) - Sale (packets)
  Total Ready KG = SUM over sizes(BAL packets x pack weight kg) + Loose KG

Negative BAL values are preserved (never clamped) and reported as
reconciliation warnings.

Open investigation — Gram Flour 200g row: RESOLVED (explanation A confirmed).
  Stock 1163, Sale 1205 -> BAL -42; Loose 58.50
  Simple formula: (-42 x 0.200) + 58.50 = 50.10 kg
  Reference report shows 54.50 kg (gap was +4.40 kg = +22 packets @200g).
  Resolution: a +22 packet addition (opening / production / adjustment /
  return) existed outside the sheet's Stock column. With it supplied,
  Stock = 1185, BAL = -20, Total = (-20 x 0.200) + 58.50 = 54.50 kg exactly.
  Rule: every stock addition MUST enter through an explicit component
  (production_kg, opening_kg, adjustment_kg, return_kg) — never silently.
  The engine enforces this by taking each component as a separate input.
"""

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

CANONICAL_SIZES_G = [250, 200, 175, 100, 50]

# A pack name counts as a canonical size within 2% (catches "249g" typos);
# a declared pack weight contradicting its name by over 1% is flagged, because
# billing runs on the weight while the customer reads the name.
SIZE_MATCH_TOLERANCE = 0.02
WEIGHT_MISMATCH_TOLERANCE = 0.01


def _slug(name) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_") or "size"


def classify_size(name):
    """Classify a packaging size name.

    Returns (key, label, grams_or_None):
    - canonical grams -> ("250", "250g", 250) etc.
    - other parseable grams -> ("500", "500g", 500) — own dynamic column
    - loose-type -> ("loose", "Loose", None)
    - unparseable -> ("name:<slug>", raw name, None) — own dynamic column
    """
    if not name:
        return ("name:unknown", "Unknown", None)
    n = str(name).strip()
    nl = n.lower()
    if "loose" in nl:
        return ("loose", "Loose", None)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g|gm|grams?)?", nl)
    if not m:
        return ("name:" + _slug(n), n, None)
    num = float(m.group(1))
    unit = (m.group(2) or "g").lower()
    grams = num * 1000 if unit == "kg" else num
    gi = int(round(grams))
    for canon in CANONICAL_SIZES_G:
        if abs(grams - canon) / canon < SIZE_MATCH_TOLERANCE:  # 2% tolerance
            return (str(canon), f"{canon}g", canon)
    glabel = n  # keep the user's own wording ("500g", "1kg", ...)
    return (str(gi if gi == grams else grams), glabel, grams)


def normalize_size_bucket(name) -> object | None:
    """Map a packaging size name to a canonical bucket.

    Returns grams (int) for the five canonical buckets, "loose" for
    loose-powder type sizes, or None when the name cannot be classified.
    Never raises.
    """
    if not name:
        return None
    n = str(name).strip().lower()
    if "loose" in n:
        return "loose"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g|gm|grams?)?", n)
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "g").lower()
    grams = num * 1000 if unit == "kg" else num
    for canon in CANONICAL_SIZES_G:
        if abs(grams - canon) / canon < SIZE_MATCH_TOLERANCE:  # 2% tolerance
            return canon
    return None


def check_weight_matches_name(name, weight_kg) -> str | None:
    """Warn when a size's weight contradicts its own name (data-entry slip)."""
    if not name:
        return None
    n = str(name).strip().lower()
    if "loose" in n:
        return None  # bulk sizes have no name-implied weight
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g|gm|grams?)?", n)
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "g").lower()
    implied_kg = num if unit == "kg" else num / 1000
    try:
        actual = float(weight_kg or 0)
    except Exception:
        return None
    if (
        implied_kg > 0
        and actual > 0
        and abs(actual - implied_kg) / implied_kg > WEIGHT_MISMATCH_TOLERANCE
    ):
        return (
            f"pack weight {actual} kg does not match name '{name}' "
            f"(implies {implied_kg} kg) — verify master data"
        )
    return None


def _d(value) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0))
    except Exception:
        return Decimal("0")


def _q2(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@dataclass
class SizeBucketInput:
    """All inputs for one product x pack-size bucket."""

    label: str = ""  # e.g. "250g"
    pack_weight_kg: Decimal = Decimal("0")
    production_kg: Decimal = Decimal("0")
    opening_kg: Decimal = Decimal("0")
    adjustment_kg: Decimal = Decimal("0")  # signed
    return_kg: Decimal = Decimal("0")
    wastage_kg: Decimal = Decimal("0")
    extra_packets: Decimal = Decimal("0")  # packets recorded directly, if any
    sale_packets: Decimal = Decimal("0")  # from challan line items
    movement_sale_kg: Decimal | None = None  # SALES-movement kg, for cross-check


@dataclass
class SizeBucketResult:
    label: str = ""
    pack_weight_kg: float = 0.0
    stock_packets: float = 0.0
    sale_packets: float = 0.0
    bal_packets: float = 0.0
    bal_kg: float = 0.0
    warnings: list[str] = field(default_factory=list)


def compute_size_bucket(inp: SizeBucketInput) -> SizeBucketResult:
    """BAL = Stock - Sale. Negatives preserved. Returns warnings."""
    res = SizeBucketResult(label=inp.label)
    weight = _d(inp.pack_weight_kg)
    res.pack_weight_kg = float(weight)
    warnings: list[str] = []

    if weight <= 0:
        # Weight unknown: packets cannot be derived from kg.
        stock_packets = _d(inp.extra_packets)
        warnings.append(f"{inp.label or 'size'}: pack weight missing, kg-based stock ignored")
    else:
        stock_kg = (
            _d(inp.production_kg)
            + _d(inp.opening_kg)
            + _d(inp.adjustment_kg)
            + _d(inp.return_kg)
            - _d(inp.wastage_kg)
        )
        stock_packets = stock_kg / weight + _d(inp.extra_packets)

    sale_packets = _d(inp.sale_packets)
    bal = stock_packets - sale_packets

    # Reconciliation cross-check: challan-item packets vs SALES-movement kg.
    if inp.movement_sale_kg is not None and weight > 0:
        movement_pkts = _d(inp.movement_sale_kg) / weight
        if abs(movement_pkts - sale_packets) > Decimal("0.001"):
            warnings.append(
                f"{inp.label or 'size'}: challan packets ({sale_packets}) != "
                f"movement packets ({movement_pkts}) — possible missing/duplicated movement"
            )

    if bal < 0:
        warnings.append(
            f"{inp.label or 'size'}: negative BAL ({bal} packets) — reconciliation required"
        )

    res.stock_packets = float(stock_packets)
    res.sale_packets = float(sale_packets)
    res.bal_packets = float(bal)
    res.bal_kg = float(bal * weight) if weight > 0 else 0.0
    res.warnings = warnings
    return res


@dataclass
class ProductAnalysisResult:
    product_id: int = 0
    product_name: str = ""
    category: str | None = None
    buckets: dict[str, SizeBucketResult] = field(default_factory=dict)
    loose_kg: float = 0.0
    total_ready_kg: float = 0.0
    warnings: list[str] = field(default_factory=list)


def compute_total_ready(buckets: list[SizeBucketResult], loose_kg) -> float:
    """Total Ready KG = SUM(BAL packets x pack weight) + Loose KG."""
    total = sum((_d(b.bal_packets) * _d(b.pack_weight_kg) for b in buckets), Decimal("0"))
    total += _d(loose_kg)
    return _q2(total)


def analyze_product(
    product_id: int,
    product_name: str,
    category: str | None,
    bucket_inputs: list[SizeBucketInput],
    loose_kg,
) -> ProductAnalysisResult:
    """Full per-product analysis using the reusable bucket engine."""
    res = ProductAnalysisResult(product_id=product_id, product_name=product_name, category=category)
    for inp in bucket_inputs:
        b = compute_size_bucket(inp)
        res.buckets[inp.label] = b
        res.warnings.extend(b.warnings)
    res.loose_kg = float(_d(loose_kg))
    res.total_ready_kg = compute_total_ready(list(res.buckets.values()), loose_kg)
    return res


# ---------------------------------------------------------------------------
# Catalog aggregation (DB rows in -> JSON-ready analysis out).
# Each size dict: size_id, size_name, weight_kg, production_kg, opening_kg,
# adjustment_kg, return_kg, wastage_kg, extra_packets, sale_packets,  # noqa: ERA001 (schema doc)
# movement_sale_kg. Loose-type sizes aggregate to loose_kg (net of ALL
# movement types including sales). Non-matching non-loose sizes merge into
# an "other" bucket. Canonical buckets always present: 250/200/175/100/50.
# ---------------------------------------------------------------------------


def _r2(x) -> float:
    return float(_d(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _r3(x) -> float:
    return float(_d(x).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def build_analysis(products: list[dict]) -> dict:
    """Aggregate products into rows with DYNAMIC size columns.

    Columns: canonical sizes that appear in data (250/200/175/100/50) + one column per
    extra size found in the data (e.g. 500g, 1kg) + Loose KG + Total Ready KG.
    Every row carries every column (zeros when absent) so the frontend can
    render without guards.
    """
    # Discover which canonical sizes are actually used in the data
    used_canonical = set()
    has_loose = False
    extra_cols: dict[str, dict] = {}
    for p in products:
        for s in p.get("sizes", []):
            key, label, grams = classify_size(s.get("size_name"))
            if key == "loose":
                has_loose = True
                continue
            if key in [str(g) for g in CANONICAL_SIZES_G]:
                used_canonical.add(key)
                continue
            if key not in extra_cols:
                extra_cols[key] = {
                    "key": key,
                    "label": label,
                    "grams": grams if isinstance(grams, (int, float)) else None,
                }
    extras_sorted = sorted(
        extra_cols.values(), key=lambda c: (c["grams"] is None, -(c["grams"] or 0), c["label"])
    )
    columns = [
        {"key": str(g), "label": f"{g}g", "grams": g}
        for g in CANONICAL_SIZES_G
        if str(g) in used_canonical
    ] + extras_sorted
    if has_loose:
        columns.append({"key": "loose", "label": "Loose", "grams": None})

    rows = []
    t_stock = t_sale = t_bal = Decimal("0")
    t_loose = t_ready = Decimal("0")

    for p in products:
        buckets: dict[str, dict] = {}
        loose_kg = Decimal("0")
        warnings: list[str] = []

        def empty_bucket(weight=None):
            return {
                "stock_packets": 0.0,
                "sale_packets": 0.0,
                "bal_packets": 0.0,
                "bal_kg": 0.0,
                "pack_weight_kg": weight,
                "sizes": [],
            }

        for s in p.get("sizes", []):
            weight = _d(s.get("weight_kg"))
            key, label, _grams = classify_size(s.get("size_name"))

            # Loose powder is counted in kg: one unit is one kg, so a
            # missing pack weight means 1 rather than "unknown" (which would
            # ignore the kg stock entirely). It then flows through the same
            # Stock/Sale/BAL bucket as every named size.
            if key == "loose" and weight <= 0:
                weight = Decimal("1")

            inp = SizeBucketInput(
                label=label,
                pack_weight_kg=weight,
                production_kg=_d(s.get("production_kg")),
                opening_kg=_d(s.get("opening_kg")),
                adjustment_kg=_d(s.get("adjustment_kg")),
                return_kg=_d(s.get("return_kg")),
                wastage_kg=_d(s.get("wastage_kg")),
                extra_packets=_d(s.get("extra_packets")),
                sale_packets=_d(s.get("sale_packets")),
                movement_sale_kg=(
                    None if s.get("movement_sale_kg") is None else _d(s.get("movement_sale_kg"))
                ),
            )
            b = compute_size_bucket(inp)
            warnings.extend(b.warnings)

            wmsg = check_weight_matches_name(s.get("size_name"), s.get("weight_kg"))
            if wmsg:
                warnings.append(f"{p.get('product_name')}: {wmsg}")

            if key not in buckets:
                buckets[key] = empty_bucket(float(weight))
                buckets[key]["sizes"].append(s.get("size_name"))
            else:
                if s.get("size_name") not in buckets[key]["sizes"]:
                    buckets[key]["sizes"].append(s.get("size_name"))
                if not buckets[key]["pack_weight_kg"]:
                    buckets[key]["pack_weight_kg"] = float(weight)
                warnings.append(f"{p.get('product_name')}: duplicate size bucket {label} merged")
            prev = buckets[key]
            prev["stock_packets"] = _r2(Decimal(str(prev["stock_packets"])) + _d(b.stock_packets))
            prev["sale_packets"] = _r2(Decimal(str(prev["sale_packets"])) + _d(b.sale_packets))
            prev["bal_packets"] = _r2(Decimal(str(prev["bal_packets"])) + _d(b.bal_packets))
            prev["bal_kg"] = _r3(Decimal(str(prev["bal_kg"])) + _d(b.bal_kg))

        # Every row carries every column (zeros when the product lacks it).
        for col in columns:
            if col["key"] not in buckets:
                w = col["grams"] / 1000 if col["grams"] else None
                buckets[col["key"]] = empty_bucket(w)

        bal_kg_sum = sum((_d(bk["bal_kg"]) for bk in buckets.values()), Decimal("0"))
        ready = bal_kg_sum + _d(loose_kg)

        for bk in buckets.values():
            t_stock += _d(bk["stock_packets"])
            t_sale += _d(bk["sale_packets"])
            t_bal += _d(bk["bal_packets"])
        t_loose += _d(loose_kg)
        t_ready += ready

        rows.append(
            {
                "product_id": p.get("product_id"),
                "product_name": p.get("product_name"),
                "category": p.get("category"),
                "buckets": buckets,
                "loose_kg": _r3(loose_kg),
                "total_ready_kg": _q2(ready),
                "warnings": warnings,
            }
        )

    return {
        "sizes": CANONICAL_SIZES_G,
        "columns": columns,
        "has_other": False,
        "rows": rows,
        "totals": {
            "stock_packets": _r2(t_stock),
            "sale_packets": _r2(t_sale),
            "bal_packets": _r2(t_bal),
            "loose_kg": _r3(t_loose),
            "total_ready_kg": _q2(t_ready),
        },
    }
