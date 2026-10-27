"""Frozen company / customer details for printed challans.

A printed challan is a document somebody keeps. If it looked up the company
address or the customer's GSTIN at print time, then correcting a typo in a
master record would silently rewrite the wording on documents that have already
been handed over. Worse, it would change them retroactively, with no record that
it happened.

So every value the printer shows is copied onto the challan when the challan is
created and never looked up again:

  * seller  - company name, address, GSTIN, phone, email, terms
  * buyer   - customer company, address, GSTIN, contact number
  * lines   - product name and pack size name

Quantities, weights, rates and amounts were already stored on the line itself,
so this closes the last route by which a printed challan could drift.

`company_snapshot_at` records when the freeze happened. It is a separate marker
rather than a "is company_name set?" test, because a deliberately blank company
name is a legitimate value and must not be mistaken for a missing snapshot.

Ordering rules that matter:
  * `apply_snapshot` is called on every creation path - the challan route AND
    the Excel importer. Missing one would leave bulk-imported challans blank.
  * `stamp_unstamped` only ever touches rows with a NULL marker, so saving the
    company profile a second time cannot alter a challan that is already frozen.
    That is what makes the first save backfill history while later saves stay
    non-destructive.
"""

from datetime import datetime, timezone

from backend.app.models import (
    ApplicationSetting,
    PackagingSize,
    PrintFooter,
    Product,
    SalesChallan,
    SalesChallanItem,
)
from backend.app.models.sales import ChallanStatus

# Setting keys holding the company profile.
COMPANY_KEYS = {
    "name": "company.name",
    "address": "company.address",
    "gstin": "company.gstin",
    "phone": "company.phone",
    "email": "company.email",
    "terms": "company.terms",
}

# Columns on sales_challans that hold the frozen seller + buyer details, in the
# order apply_snapshot writes them.
SNAPSHOT_FIELDS = (
    "company_name",
    "company_address",
    "company_gstin",
    "company_phone",
    "company_email",
    "company_terms",
    "cust_company_name",
    "cust_address",
    "cust_gst_no",
    "cust_contact_number",
)


def company_profile(db) -> dict:
    """Read the company profile from application_settings.

    Reads ApplicationSetting directly rather than going through main.py's
    _get_setting helper, so this module stays free of any dependency on the
    route layer (excel_import imports it). One query for all six keys, and a
    missing key is simply an empty field rather than an error.
    """

    wanted = set(COMPANY_KEYS.values())
    found = {
        r.key: (r.value or "").strip()
        for r in db.query(ApplicationSetting).filter(ApplicationSetting.key.in_(wanted)).all()
    }
    return {field: found.get(key, "") for field, key in COMPANY_KEYS.items()}


def customer_snapshot(customer) -> dict:
    """The customer's printed details, or blanks when there is no customer."""
    if customer is None:
        return {"company_name": None, "address": None, "gst_no": None, "contact_number": None}
    return {
        "company_name": (customer.company_name or "").strip() or None,
        "address": (customer.address or "").strip() or None,
        "gst_no": (customer.gst_no or "").strip() or None,
        "contact_number": (customer.contact_number or "").strip() or None,
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def apply_snapshot(challan, company: dict, customer=None, when=None) -> None:
    """Freeze the seller, buyer and stamp time onto a challan.

    The customer arguments are all optional so a caller with nothing but the
    profile can still stamp the seller half.
    """
    company = company or {}
    cust = customer_snapshot(customer)
    challan.company_name = (company.get("name") or "").strip() or None
    challan.company_address = (company.get("address") or "").strip() or None
    challan.company_gstin = (company.get("gstin") or "").strip() or None
    challan.company_phone = (company.get("phone") or "").strip() or None
    challan.company_email = (company.get("email") or "").strip() or None
    challan.company_terms = (company.get("terms") or "").strip() or None
    challan.cust_company_name = cust["company_name"]
    challan.cust_address = cust["address"]
    challan.cust_gst_no = cust["gst_no"]
    challan.cust_contact_number = cust["contact_number"]
    challan.company_snapshot_at = when or _now()


def is_stamped(challan) -> bool:
    """True once this challan has a frozen header."""
    return getattr(challan, "company_snapshot_at", None) is not None


def default_footer(db):
    """Configured default print footer as (id_or_None, body_text).

    An explicitly picked footer is validated by the challan route; this is
    only the fallback both it and the Excel importer freeze when no footer
    is picked. Returns (None, "") when no usable default exists, so callers
    fall back to the legacy company.terms profile text.
    """
    did = (
        db.query(ApplicationSetting)
        .filter(ApplicationSetting.key == "sales.footer_id")
        .first()
    )
    if not did or not (did.value or "").strip():
        return None, ""
    try:
        fid = int((did.value or "").strip())
    except (TypeError, ValueError):
        return None, ""
    f = (
        db.query(PrintFooter)
        .filter(PrintFooter.id == fid, PrintFooter.is_active == True)  # noqa: E712
        .first()
    )
    if not f:
        return None, ""
    return f.id, (f.body or "").strip()


def stamp_unstamped(db, company: dict, customer_lookup=None, when=None) -> int:
    """Stamp every challan that has no snapshot yet. Returns the count.

    This is the backfill for challans created before the company profile
    existed: the first save freezes the profile onto them, and because only
    NULL rows are touched, saving again later changes nothing already frozen.

    The buyer's details are filled from the customer the challan points at, so
    each historic challan keeps the address of the customer it was issued to.
    """

    rows = db.query(SalesChallan).filter(SalesChallan.company_snapshot_at.is_(None)).all()
    if not rows:
        return 0

    stamp_at = when or _now()
    # One query for every referenced customer rather than one per challan.
    cust_ids = {c.customer_id for c in rows if c.customer_id}
    customers = {}
    if customer_lookup is not None:
        for c in cust_ids:
            found = customer_lookup(c)
            if found is not None:
                customers[c] = found

    for c in rows:
        apply_snapshot(c, company, customers.get(c.customer_id), stamp_at)
    return len(rows)


def fill_missing_item_names(db) -> int:
    """Freeze product/size names on lines that predate the snapshot.

    `stamp_unstamped` only handles the challan header, so without this the 89
    challans that predate the snapshot would keep NULL item names and a later
    product rename would still rewrite their printed lines - the exact thing the
    freeze exists to prevent.

    Resolved with two lookups of the master tables rather than one per line.
    Only lines missing BOTH names are filled, so a real value is never
    overwritten, and re-running is harmless.
    """

    rows = db.query(SalesChallanItem).filter(SalesChallanItem.product_name.is_(None)).all()
    if not rows:
        return 0

    products = {p.id: p.name for p in db.query(Product).all()}
    sizes = {s.id: s.size_name for s in db.query(PackagingSize).all()}

    filled = 0
    for it in rows:
        name = products.get(it.product_id)
        size = sizes.get(it.packaging_size_id)
        if name and size:
            it.product_name = name
            it.size_name = size
            filled += 1
    return filled


def describe(challan) -> dict:
    """Snapshot view for API responses and printing.

    Returns the frozen values plus a `stamped` flag so the UI can say why a
    header is blank instead of silently printing nothing.
    """
    data = {f: getattr(challan, f, None) for f in SNAPSHOT_FIELDS}
    data["company_snapshot_at"] = getattr(challan, "company_snapshot_at", None)
    data["stamped"] = is_stamped(challan)
    return data


def all_challan_balances(db) -> dict:
    """Return {challan_id: outstanding_amount} for non-archived, non-draft challans.

    A draft challan is not yet confirmed, so it is not a receivable. Dashboard
    and customer/report pages look up a challan's current balance by id;
    missing keys default to 0 so archived/paid/draft challans show nothing owed.
    """
    rows = (
        db.query(SalesChallan.id, SalesChallan.outstanding_amount)
        .filter(
            SalesChallan.is_archived == False,  # noqa: E712
            SalesChallan.status != ChallanStatus.DRAFT,
        )
        .all()
    )
    return {r.id: float(r.outstanding_amount or 0) for r in rows}
