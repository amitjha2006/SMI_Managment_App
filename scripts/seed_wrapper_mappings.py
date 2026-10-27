"""Every pack size points at the pouch its packets are made from.

A size carries `wrapper_material_id` so production can pick the printed roll
itself and weigh the pouches against the batch. A repair run makes that true
for the whole catalogue in one pass, three phases:

1. rename - roll names were typed by hand ("Pauch Packet label red chili
   50g"), so they are rewritten to one scheme: "Pouch <Product> (<Hindi>)
   <Size>", and the unbranded roll becomes "Pouch Loose Powder 1kg".
   The "Prining" typo on the printing cylinders is fixed in the same pass.
2. create - a size with no roll gets its own pouch raw material (Packaging
   category, kg, HSN 39199090, zero stock: the real roll is bought later).
3. map - every size is pointed at its roll. Loose Powder uses the one shared
   1 kg pouch; inactive products are left alone (a size with no pouch cannot
   be produced wrongly, it just falls back to picking a roll by hand).

Only Packaging-category materials are eligible rolls. The Turmeric printing
cylinder sits in the Cylinder category and its name also says "Turmeric
250g" - treating it as a roll tied the size to a printing drum.

Safety rules (see AGENTS.md):
- refuses the repo's live database path outright,
- dry run unless --apply is given,
- --apply refused without --backup,
- idempotent: a second run plans nothing, and a size already pointing at a
  roll keeps it (a conflict is reported, never overwritten).

Run (from the repo root, laptop app CLOSED):
    python scripts/seed_wrapper_mappings.py --db <path>                # dry run
    python scripts/seed_wrapper_mappings.py --db <path> --apply --backup
"""

import argparse
import contextlib
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from backend.app import models  # noqa: E402

REPO_LIVE_DB = os.path.join(ROOT, "data", "masala_factory.db")

PACKAGING = "Packaging"
LOOSE_SIZE = "Loose Powder"
LOOSE_POUCH = "Pouch Loose Powder 1kg"
POUCH_HSN = "39199090"
# "Cumin seeds" has been switched off but its only size is Loose Powder, and
# the owner asked for it to keep the shared pouch so reviving it needs nothing.
LOOSE_EXTRA_PRODUCTS = ("cumin seeds",)

# Shorter product keys ("1", "kg") would match half the catalogue by accident.
MIN_KEY_LEN = 3


def norm(value):
    """Plain single-spaced text: the data has non-breaking and double spaces."""
    return re.sub(r"\s+", " ", (value or "").replace("\xa0", " ")).strip()


def ensure_column(db):
    """The app adds wrapper_material_id on startup; a repair run may come
    first, and the model now selects it."""
    cols = [row[1] for row in db.execute(text("PRAGMA table_info(packaging_sizes)"))]
    if "wrapper_material_id" not in cols:
        db.execute(text("ALTER TABLE packaging_sizes ADD COLUMN wrapper_material_id INTEGER"))
        db.commit()
        print("note: added packaging_sizes.wrapper_material_id (the app does this on startup)")


def split_name(product_name):
    """The product's English and Hindi names, minus the word "powder".

    "Red Chili Powder (लाल मिर्च पाउडर)" -> ("Red Chili", "लाल मिर्च").
    """
    base = norm(product_name)
    found = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", base)
    english, hindi = (found.group(1), found.group(2)) if found else (base, "")
    english = re.sub(r"\bpowder\b", "", english, flags=re.I).strip()
    hindi = re.sub(r"\bपाउडर\s*$", "", hindi).strip()
    return norm(english), norm(hindi)


def pouch_name(product, size_name):
    english, hindi = split_name(product.name)
    if hindi:
        return f"Pouch {english} ({hindi}) {size_name}"
    return f"Pouch {english} {size_name}"


def product_key(name):
    """The matching key: English name only, no Hindi, no "powder".

    "Turmeric Powder (हल्दी पाउडर) -mass" -> "turmeric -mass".
    """
    base = re.sub(r"\([^)]*\)", " ", norm(name))
    base = re.sub(r"\bpowder\b", " ", base, flags=re.I)
    return norm(base).casefold()


def size_in(text_, size_name):
    """True when the size appears as a whole token.

    Plain `in` is wrong here: "50g" is a substring of "250g".
    """
    if not size_name:
        return False
    pattern = r"(?<![0-9a-z])" + re.escape(size_name.casefold()) + r"(?![0-9a-z])"
    return re.search(pattern, text_) is not None


def is_loose(size_name):
    return norm(size_name).casefold() == LOOSE_SIZE.casefold()


def looks_like_pouch(name):
    low = norm(name).casefold()
    return "pauch" in low or "pouch" in low or "packet label" in low


def fix_typos(name):
    return re.sub(r"\bprining\b", "Printing", norm(name), flags=re.I)


def category_id(db, name):
    cat = (
        db.query(models.RawMaterialCategory)
        .filter(models.RawMaterialCategory.name == name)
        .first()
    )
    return cat.id if cat else None


def next_codes(db, count):
    """Fresh RM-xxxx codes from the packaging block (RM-1002..RM-1008 today)."""
    used = {
        (code or "").upper()
        for (code,) in db.query(models.RawMaterial.material_id).all()
    }
    numbers = [
        int(m.group(1))
        for code in used
        if (m := re.fullmatch(r"RM-(1\d{3})", code))
    ]
    start = max(numbers, default=1000) + 1
    codes = []
    while len(codes) < count:
        candidate = f"RM-{start:04d}"
        start += 1
        if candidate.upper() in used:
            continue
        codes.append(candidate)
        used.add(candidate.upper())
    return codes


def _pouch_hits(roll_name, products):
    """(product, size) pairs a hand-typed roll name describes."""
    found = []
    for product in products:
        key = product_key(product.name)
        if len(key) < MIN_KEY_LEN or key not in roll_name.casefold():
            continue
        found.extend(
            (product, size)
            for size in product.packaging_sizes
            if size.is_active is not False
            and not is_loose(size.size_name)
            and size_in(roll_name.casefold(), norm(size.size_name).casefold())
        )
    return found


def _plan_names(plan, materials, products):
    """Phase 1. Returns {material_id: planned name}, lowercased for lookups."""
    effective = {}
    for material in materials:
        if material.is_active is False:
            continue
        current = norm(material.name)
        target = fix_typos(current)
        if material.category_id != plan["category_id"]:
            # Not a packaging roll, but the spelling pass still applies
            # ("Gravure Prining Cylinder").
            if target != current:
                plan["rename"].append((material, target))
            continue
        if looks_like_pouch(target):
            hits = _pouch_hits(target, products)
            if not hits:
                target = LOOSE_POUCH  # the roll nobody labelled
            elif len(hits) == 1:
                target = pouch_name(hits[0][0], hits[0][1].size_name)
            else:
                where = ", ".join(f"{p.name} / {s.size_name}" for p, s in hits)
                plan["conflict"].append(
                    (None, f"roll '{current}' describes {where} - left as it is")
                )
                effective[material.id] = current.casefold()
                continue
        if target != current:
            plan["rename"].append((material, target))
        effective[material.id] = target.casefold()
    return effective


def _plan_sizes(db, plan, rolls, effective, products_by_id):
    """Phases 2 and 3: one pouch per size, Loose Powder shares one."""
    by_name = {}
    for roll in rolls:
        by_name.setdefault(effective[roll.id], roll)

    for size in db.query(models.PackagingSize).order_by(models.PackagingSize.id):
        product = products_by_id.get(size.product_id)
        if product is None or size.is_active is False:
            continue
        loose = is_loose(size.size_name)
        revives_loose = loose and product_key(product.name) in LOOSE_EXTRA_PRODUCTS
        if product.is_active is False and not revives_loose:
            plan["skip"].append((product, size, "product inactive"))
            continue
        target = LOOSE_POUCH if loose else pouch_name(product, size.size_name)
        where = f"{product.name} / {size.size_name}"

        roll = by_name.get(target.casefold())
        if roll is not None:
            if size.wrapper_material_id == roll.id:
                plan["already"].append((size, target))
            elif size.wrapper_material_id is None:
                plan["map"].append((size, target))
            else:
                other = db.get(models.RawMaterial, size.wrapper_material_id)
                kept = other.name if other else size.wrapper_material_id
                plan["conflict"].append((size, f"{where} keeps '{kept}', not '{target}'"))
            continue

        if size.wrapper_material_id is not None:
            other = db.get(models.RawMaterial, size.wrapper_material_id)
            kept = other.name if other else size.wrapper_material_id
            plan["conflict"].append(
                (
                    size,
                    f"{where} points at '{kept}', which is not a {PACKAGING} roll "
                    "- clear it in the app, then re-run",
                )
            )
            continue

        # A pouch this run already plans to create is shared (Loose Powder).
        group = next((g for g in plan["create"] if g[0] == target), None)
        if group is None:
            lookalike = [
                m
                for m in rolls
                if not loose
                and product_key(product.name) in effective[m.id]
                and size_in(effective[m.id], norm(size.size_name).casefold())
            ]
            if lookalike:
                plan["conflict"].append(
                    (
                        size,
                        f"{where}: '{norm(lookalike[0].name)}' looks like this pouch but is "
                        f"not named '{target}' - rename it, then re-run",
                    )
                )
                continue
            group = (target, [])
            plan["create"].append(group)
        group[1].append(size)
        plan["map"].append((size, target))


def build_plan(db):
    """Everything one run would do, in apply order: rename -> create -> map."""
    plan = {
        "rename": [],   # (material, new_name)
        "create": [],   # (name, [sizes])
        "map": [],      # (size, roll_name)
        "already": [],  # (size, roll_name)
        "skip": [],     # (product, size, reason)
        "conflict": [],  # (size, message)
        "category_id": category_id(db, PACKAGING),
    }
    if plan["category_id"] is None:
        plan["skip"].append((None, None, f"no '{PACKAGING}' raw-material category"))
        return plan

    materials = list(db.query(models.RawMaterial).order_by(models.RawMaterial.id))
    rolls = [
        m
        for m in materials
        if m.is_active is not False and m.category_id == plan["category_id"]
    ]
    all_products = list(db.query(models.Product).order_by(models.Product.id))
    products = [p for p in all_products if p.is_active is not False]
    products_by_id = {p.id: p for p in all_products}

    effective = _plan_names(plan, materials, products)
    _plan_sizes(db, plan, rolls, effective, products_by_id)
    return plan


def report(plan):
    print("renames")
    print("-" * 78)
    for roll, new in plan["rename"] or []:
        print(f"  {roll.id:>3} '{norm(roll.name)}' -> '{new}'")
    if not plan["rename"]:
        print("  none")

    print("\nnew pouch raw materials")
    print("-" * 78)
    for name, sizes in plan["create"] or []:
        where = ", ".join(f"{s.size_name}" for s in sizes)
        print(f"  {name:<44} {where}")
    if not plan["create"]:
        print("  none")

    print("\nmappings")
    print("-" * 78)
    for size, name in plan["map"] or []:
        print(f"  size {size.id:>3} {size.size_name:<14} -> {name}")
    if not plan["map"]:
        print("  none")
    print(f"  already correct: {len(plan['already'])}")

    if plan["conflict"]:
        print(f"\nconflicts, left alone ({len(plan['conflict'])}):")
        for _size, message in plan["conflict"]:
            print(f"  {message}")
    if plan["skip"]:
        print(f"\nskipped ({len(plan['skip'])}):")
        for product, size, reason in plan["skip"]:
            where = f"{product.name} / {size.size_name}" if product else "database"
            print(f"  {where}: {reason}")


def apply_plan(db, plan):
    for roll, new in plan["rename"]:
        roll.name = new
    db.flush()

    existing = {
        norm(m.name).casefold(): m
        for m in db.query(models.RawMaterial).all()
        if m.category_id == plan["category_id"]
    }
    codes = next_codes(db, len(plan["create"]))
    for (name, _sizes), code in zip(plan["create"], codes, strict=True):
        mat = models.RawMaterial(
            material_id=code,
            name=name,
            category_id=plan["category_id"],
            unit="KG",
            opening_stock=0,
            current_stock=0,
            threshold_stock=0,
            reorder_level=0,
            purchase_rate=0,
            hsn_code=POUCH_HSN,
            watch_stock=False,
            is_active=True,
        )
        db.add(mat)
        db.flush()
        existing[name.casefold()] = mat
        print(f"  created {code} {name}")

    for size, name in plan["map"]:
        size.wrapper_material_id = existing[name.casefold()].id
    db.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to the database file to update.")
    parser.add_argument("--apply", action="store_true",
                        help="Write the changes. Without this the script only reports.")
    parser.add_argument("--backup", action="store_true",
                        help="Copy the database to <db>.seed_wrapper_bak before writing.")
    args = parser.parse_args()

    # Product names contain Devanagari; the default console cannot encode them,
    # which must not abort a report mid-way.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError):
            stream.reconfigure(encoding="utf-8", errors="replace")

    db_path = os.path.abspath(args.db)
    errors = []
    if not os.path.exists(db_path):
        errors.append(f"database not found: {db_path}")
    if os.path.abspath(REPO_LIVE_DB) == os.path.abspath(db_path):
        errors.append(
            "refusing the repo's live data/masala_factory.db - run this "
            "against the laptop database (or a copy of it)."
        )
    if args.apply and not args.backup:
        errors.append(
            "--apply without --backup is refused; copy the database yourself "
            "or pass --backup to have the script do it."
        )
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        return 2

    engine = create_engine(f"sqlite:///{db_path}")
    db = sessionmaker(bind=engine)()
    try:
        ensure_column(db)
        plan = build_plan(db)
        report(plan)
        if not (plan["rename"] or plan["create"] or plan["map"]):
            print("nothing to do.")
            return 0
        if not args.apply:
            print("dry run - nothing written. Re-run with --apply (and --backup) to write.")
            return 0
        if args.backup:
            bak = f"{db_path}.seed_wrapper_bak"
            shutil.copy2(db_path, bak)
            print(f"backup written: {bak}")
        apply_plan(db, plan)
        print(
            f"written: {len(plan['rename'])} renamed, {len(plan['create'])} pouches "
            f"created, {len(plan['map'])} mappings."
        )
        again = build_plan(db)
        left = len(again["rename"]) + len(again["create"]) + len(again["map"])
        if left:
            print(f"verified: re-plan still wants {left} change(s) - INVESTIGATE")
        else:
            print("verified: re-plan wants nothing more")
        return 0 if left == 0 else 1
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
