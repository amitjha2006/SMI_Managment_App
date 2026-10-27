"""IST-everywhere: the app default timezone is India, for everything.

Rule: every timestamp the app writes is IST wall time, stored naive
(SQLite cannot keep offsets). Machine-clock reads (datetime.now(),
datetime.utcnow()) and UTC server defaults (func.now()) are banned from
persistence paths - they silently mix UTC-wall and IST-wall rows in one
table, which is how session scoping and ageing drifted by hours.

Covers: core helper, main.py routes, model defaults, the importer,
serializers, and the frontend parser.

Run: python -m pytest backend/tests/test_ist_everywhere.py -v
"""

import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _read(rel):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_ist_wall_is_naive_ist_now():
    from backend.app.core.database import _ist_wall

    got = _ist_wall()
    assert got.tzinfo is None, "stored values must stay naive (SQLite drops offsets)"
    want = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    assert abs((got - want.replace(tzinfo=None)).total_seconds()) < 120, (
        f"_ist_wall ({got}) is not India time"
    )


def _bare_clock_lines(path):
    """Lines reading the machine clock instead of IST."""
    bad = []
    for n, line in enumerate(_read(path).splitlines(), 1):
        code = line.split("#", 1)[0]
        # expires_delta is purely relative arithmetic (two UTC instants
        # subtracted); it never lands in storage or a comparison.
        if "expires_delta" in code:
            continue
        if "datetime.now()" in code or "datetime.utcnow()" in code:
            bad.append(f"{path}:{n}: {line.strip()}")
        if re.search(r"datetime\.now\(timezone\.utc\)", code):
            bad.append(f"{path}:{n}: {line.strip()}")
    return bad


def test_no_bare_clock_writes_in_routes():
    bad = _bare_clock_lines("backend/main.py")
    assert not bad, "machine-clock reads in routes (use _ist_wall):\n" + "\n".join(bad)


def test_no_bare_clock_writes_in_importer():
    bad = _bare_clock_lines("backend/app/services/excel_import.py")
    assert not bad, "machine-clock reads in importer (use _ist_wall):\n" + "\n".join(bad)


def test_no_utc_server_defaults_in_models():
    import glob

    bad = []
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for path in sorted(glob.glob(os.path.join(root, "backend", "app", "models", "*.py"))):
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        for n, line in enumerate(lines, 1):
            code = line.split("#", 1)[0]
            if "server_default=func.now()" in code or "onupdate=func.now()" in code:
                bad.append(f"{os.path.basename(path)}:{n}: {line.strip()}")
    assert not bad, (
        "UTC server defaults (use default=_ist_wall so new rows land in IST):\n"
        + "\n".join(bad)
    )


def test_iso_serializer_stamps_ist_not_utc():
    import backend.main as m
    from backend.app.services import pdf_export  # noqa: F401  (import side-effect free)

    assert hasattr(m, "_iso_ist"), "serializer must be IST-native"
    assert not hasattr(m, "_iso_utc"), "UTC-stamping serializer must be gone"
    out = m._iso_ist(datetime(2026, 9, 10, 8, 30))
    assert out.endswith("+05:30"), f"naive input must stamp IST offset, got {out}"
    out2 = m._iso_ist(datetime(2026, 9, 10, 8, 30, tzinfo=timezone.utc))
    assert "14:00" in out2 and out2.endswith("+05:30"), f"UTC input must convert, got {out2}"


def test_frontend_treats_naive_input_as_ist():
    js = _read("frontend/js/components.js")
    i = js.find("function asUtcDate")
    assert i >= 0, "frontend datetime parser missing"
    body = js[i:i + 600]
    assert "+05:30" in body, "naive server values must parse as IST wall time"
