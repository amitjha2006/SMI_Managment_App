"""Report date sort: header clicks must reorder, both directions.

Root cause it pins: report rows carry ISO datetimes ("2026-09-10T10:00:00")
and the shared sortRows() strips every non-numeric char, so parseFloat
stops at the second dash and every date collapses to its year - all ties,
asc and desc identical, clicks appear dead. Both report tables (custom
and system presets) must sort the date column by timestamp instead.

Run: python -m pytest backend/tests/test_report_sort.py -v
"""

import os
import re


def _read():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "reports.html"), encoding="utf-8") as fh:
        return fh.read()


def _read_rel(rel):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_custom_report_date_sorts_by_timestamp():
    html = _read()
    i = html.find("window.runCustomReport = async function () {")
    assert i >= 0
    body = html[i:i + 2500]
    assert re.search(r"sortValue\s*:\s*\{[^}]*date\s*:", body), (
        "custom report table must sort the date column by timestamp"
    )


def test_system_report_date_sorts_by_timestamp():
    html = _read()
    i = html.find("window.runSysReport = async function () {")
    assert i >= 0
    body = html[i:i + 2500]
    assert re.search(r"sortValue\s*:\s*\{[^}]*date\s*:", body), (
        "system report table must sort the date column by timestamp"
    )


def test_report_invoice_number_sorts_fy_aware():
    html = _read()
    assert "window.UI.docNumKey" in html, (
        "reports page must use the shared FY-aware invoice number key "
        "(PUR/26-27/0001 collapses like challan numbers did)"
    )
    assert len(re.findall(r"invoice\s*:\s*r\s*=>\s*window\.UI\.docNumKey", html)) >= 2, (
        "both report tables must wire the invoice column to the FY-aware key"
    )


def test_recent_transactions_show_year_and_time():
    dash = _read_rel("frontend/pages/dashboard.html")
    i = dash.find("function renderRecentTxns()")
    assert i >= 0
    body = dash[i:i + 2000]
    assert "dateWithTime" in body, (
        "recent transactions must show year + IST timestamp, not day-month only"
    )
