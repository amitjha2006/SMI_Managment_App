"""Excel date serials reaching the importers as bare numbers.

Templates where the date column is not formatted as a date carry raw Excel
serials (46238 etc.). Those used to parse to nothing and the challan import
silently stamped "now" instead — challans 270/1726/273 went in with the
import day as their challan_date (Aug 2026 became Oct 15 2026). The parser
now decodes whole-number serials in a sane range back to real dates.

Run: python -m pytest backend/tests/test_excel_serial_dates.py -v
"""

import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.app.services.excel_import as xi  # noqa: E402


def test_bare_serial_decodes_to_midnight():
    # 1899-12-30 + 46238 days = 2026-08-04 (challan 270's sheet date).
    assert xi.parse_date(46238) == datetime(2026, 8, 4)
    assert xi.parse_date(46256) == datetime(2026, 8, 22)
    assert xi.parse_date(46265) == datetime(2026, 8, 31)


def test_numeric_string_serial_decodes():
    assert xi.parse_date("46238") == datetime(2026, 8, 4)
    assert xi.parse_date(" 46256 ") == datetime(2026, 8, 22)


def test_out_of_range_numbers_stay_unparsed():
    # A bare year, an id, a big quantity: none of these may become dates.
    assert xi.parse_date(2026) is None
    assert xi.parse_date("1234567") is None
    assert xi.parse_date(15000) is None  # below the factory-era floor


def test_fractional_number_is_not_a_date():
    # Time-of-day fractions / non-integer quantities stay unparsed.
    assert xi.parse_date(46238.5) is None


def test_normal_text_dates_still_work():
    assert xi.parse_date("8/4/2026") == datetime(2026, 8, 4)
    assert xi.parse_date("2026-08-04") == datetime(2026, 8, 4)
    assert xi.parse_date("04-Aug-2026") == datetime(2026, 8, 4)
    assert xi.parse_date(datetime(2026, 8, 4, 10, 30)) == datetime(2026, 8, 4, 10, 30)
    assert xi.parse_date(date(2026, 8, 4)) == datetime(2026, 8, 4)


def test_empty_and_garbage_stay_none():
    assert xi.parse_date(None) is None
    assert xi.parse_date("") is None
    assert xi.parse_date("not a date") is None
