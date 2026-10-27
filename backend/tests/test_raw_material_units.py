"""Raw materials accept pieces / litres / metres units end to end.

Units were a closed kg/gram/packet set; anything else silently became kg
on import. Run: python -m pytest backend/tests/test_raw_material_units.py -v
"""

import io
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402


def _req():
    return SimpleNamespace(headers={}, client=None)


def test_new_units_saved_via_api(db):
    for unit, mid in (("pieces", "RM-U1"), ("litres", "RM-U2"), ("metres", "RM-U3")):
        m.create_raw_material(
            m.RawMaterialCreate(material_id=mid, name=f"Unit {unit}", unit=unit),
            _req(), db,
        )
    rows = {r.material_id: r.unit for r in m.list_raw_materials(db)}
    assert rows["RM-U1"] == "pieces", rows
    assert rows["RM-U2"] == "litres", rows
    assert rows["RM-U3"] == "metres", rows


def test_import_aliases_map_to_new_units(db):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Raw Material Name", "Unit"])
    ws.append(["Pouch Roll", "PCS"])
    ws.append(["Mustard Oil", "Ltr"])
    ws.append(["Packing Tape", "mtr"])
    buf = io.BytesIO()
    wb.save(buf)
    out = m.run_import(db, "raw_materials", buf.getvalue())
    assert out["imported"] == 3, out
    rows = {r.name: r.unit for r in m.list_raw_materials(db)}
    assert rows["Pouch Roll"] == "pieces", rows
    assert rows["Mustard Oil"] == "litres", rows
    assert rows["Packing Tape"] == "metres", rows


def test_form_offers_new_units():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(
        os.path.join(root, "frontend", "pages", "raw-materials.html"), encoding="utf-8"
    ) as fh:
        html = fh.read()
    i = html.find('id="raw-unit"')
    assert i >= 0
    for unit in ("pieces", "litres", "metres"):
        assert f'value="{unit}"' in html[i:i + 600], unit
