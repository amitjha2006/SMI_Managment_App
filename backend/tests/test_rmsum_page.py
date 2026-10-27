"""Raw Material Summary as its own page + 2-decimal quantities.

The summary moves out of Reports into a dedicated page beside Stock
Analysis. Raw Materials moves under Master Data. Quantities show at most
2 decimals with trailing zeros trimmed (17.5, not 17.500), through one
shared helper, everywhere a stock figure meets the eye.

Run: python -m pytest backend/tests/test_rmsum_page.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _read(relpath):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *relpath.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_summary_route_and_nav_beside_analysis():
    app_js = _read("frontend/js/app.js")
    assert "raw-material-summary.html" in app_js, "no summary page route"
    index = _read("frontend/index.html")
    ai = index.find('href="#/analysis"')
    ri = index.find('href="#/raw-material-summary"')
    assert ai >= 0 and ri >= 0 and ai < ri, "summary link not next to Stock Analysis"


def test_raw_materials_lives_under_master_data():
    index = _read("frontend/index.html")
    master = index.find('data-section="master"')
    operations = index.find('data-section="operations"')
    link = index.find('data-page="raw-materials"')
    assert master < link < operations, "Raw Materials not under Master Data"


def test_summary_page_calls_endpoint_with_range():
    html = _read("frontend/pages/raw-material-summary.html")
    assert "/reports/raw-material-summary" in html, "page never calls the endpoint"
    for token in ["rep-rm-from", "rep-rm-to", "rmsumPreset"]:
        assert token in html, f"page misses range control {token}"


def test_reports_page_no_longer_duplicates_summary():
    html = _read("frontend/pages/reports.html")
    assert "/reports/raw-material-summary" not in html, "summary lives in two places"


def test_shared_two_decimal_helper_exists():
    js = _read("frontend/js/components.js")
    assert "qty2" in js, "no shared max-2-decimals helper"


def test_production_quantities_use_two_decimals():
    html = _read("frontend/pages/production.html")
    assert "qty2" in html, "material dropdown still shows raw 3-decimal stock"
    assert ".toFixed(3)" not in html, "formulation prefill still forces 3 decimals"


def test_raw_adjustment_quantities_use_two_decimals():
    html = _read("frontend/pages/raw-materials.html")
    assert "qty2" in html, "adjustment dropdown still shows raw 3-decimal stock"


def test_filter_rows_share_one_actions_row():
    html = _read("frontend/pages/raw-material-summary.html")
    i = html.find('id="rmsum-actions"')
    assert i >= 0, "filter buttons scattered across rows"
    block = html[i:i + 900]
    for token in ("loadRawMaterialSummary()", "rmsumPreset", "rmsumShowSelected",
                  "rmsumReset"):
        assert token in block, f"actions row misses {token}"


def test_reset_restores_default_dates():
    html = _read("frontend/pages/raw-material-summary.html")
    i = html.find("window.rmsumReset")
    assert i >= 0
    body = html[i:i + 600]
    assert 'getElementById("rep-rm-from").value = ""' in body, "Reset keeps a From date"
    assert 'getElementById("rep-rm-to").value = ""' in body, "Reset keeps a To date"
    assert "loadRawMaterialSummary()" in body, "Reset does not reload"


def test_summary_numbers_use_boxes():
    html = _read("frontend/pages/raw-material-summary.html")
    assert "rm-box" in html, "no box markup"
    i = html.find("#rep-rmsum .rm-box.rm-neg")
    assert i >= 0 and "#f43f5e" in html[i:i + 160], "no red box for negatives"
