"""Regressions for the three reported bugs.

1. Company profile terms could not be saved: the PUT was gated behind Editor
   Mode, and the Save button carried [data-approve], which sets
   pointer-events: none. So with Editor Mode off the click never reached the
   handler and nothing at all happened on screen.

2. Packet QTY displayed with decimals: the API returns decimal strings
   ("15.000"), and the challan detail view and the production views dropped them
   straight into a cell, so a packet count read "15.000". Both print views
   already used 0 decimals, so screen and document disagreed.

3. Auto-refresh re-fetched but showed old numbers: API responses carried no
   Cache-Control and the frontend never asked to skip the cache, so the WebView
   could serve the previous response.

These call the real functions rather than going through TestClient, which the
rest of the suite also avoids (it needs httpx2, which is not installed).
"""

import asyncio
import re
import sys
import types

import pytest

ROOT = r"C:\Shresth_Masala_Application"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _read(rel):
    with open(ROOT + "\\" + rel.replace("/", "\\"), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def main():
    import backend.main as m

    return m


# --------------------------------------------------------------------------
# 1. Company profile terms
# --------------------------------------------------------------------------


def test_company_profile_is_exempt_from_editor_mode(main):
    assert main._editor_mode_always_allowed("/settings/company"), (
        "/settings/company must be exempt from Editor Mode, otherwise saving "
        "the company profile is refused with 'Editor Mode is off'"
    )
    assert main._editor_mode_always_allowed("/api/dashboard/preferences") is not False


def test_exemption_does_not_leak_to_records(main):
    for p in (
        "/challans/1",
        "/sales/invoices/3",
        "/production/2",
        "/customers/1",
        "/settings/backup",
    ):
        assert not main._editor_mode_always_allowed(p), (
            f"{p} must still require Editor Mode; the exemption leaked"
        )


def test_company_profile_save_button_is_clickable():
    """[data-approve] sets pointer-events:none while Editor Mode is off."""
    settings = _read("frontend/pages/settings.html")
    btn = re.search(r"<button[^>]*onclick=\"saveCompanySettings\((event)?\)\"[^>]*>", settings)
    assert btn, "Save company profile button not found"
    assert "data-approve" not in btn.group(0), (
        "the Save company profile button still carries data-approve, so CSS sets "
        "pointer-events:none and the click silently does nothing"
    )


def test_company_settings_page_sends_terms():
    settings = _read("frontend/pages/settings.html")
    assert re.search(r'terms:\s*document\.getElementById\("co-terms"\)\.value', settings), (
        "the company profile save must send the co-terms textarea value"
    )


# --------------------------------------------------------------------------
# 2. Packet QTY decimals
# --------------------------------------------------------------------------


def test_challan_detail_formats_quantity_as_whole_packets():
    ch = _read("frontend/pages/challans.html")
    row = re.search(r"d\.items\.map\(it => `<tr>.*?</tr>`\)\.join", ch, re.S)
    assert row, "the challan detail item row was not found"
    cell = row.group(0)
    assert "fmtNum(it.quantity, 0)" in cell, (
        "the challan detail Qty cell must use fmtNum(it.quantity, 0); the raw "
        "value is the string '15.000', so a 15-packet line reads as 15.000"
    )
    assert not re.search(r"esc\(it\.quantity\)", cell), (
        "esc(it.quantity) is still present and prints the raw decimal string"
    )
    # Weights display at most 2 decimals app-wide (user rule); packets whole.
    for field, dp in (("it.weight", 2), ("it.rate_per_kg", 2), ("it.amount", 2)):
        assert f"fmtNum({field}, {dp})" in cell, f"{field} should use {dp} decimals"


def test_both_print_views_agree_with_the_detail_view():
    """The document and the on-screen view must show the same qty."""
    ch = _read("frontend/pages/challans.html")
    assert ch.count("num(it.quantity, 0)") == 2, (
        "both the challan print and the invoice print should render qty with 0 "
        f"decimals; found {ch.count('num(it.quantity, 0)')} occurrences"
    )


def test_production_views_format_packets_and_weights():
    prod = _read("frontend/pages/production.html")
    assert "fmtNum(e.actual_packets, 0)" in prod, (
        "the production table still renders actual_packets raw"
    )
    assert "fmtNum(e.actual_output, 2)" in prod, (
        "the production table Actual must show kg alongside packets"
    )
    assert "fmtNum(d.actual_packets, 0)" in prod, (
        "the production detail still renders actual_packets raw"
    )
    assert "fmtNum(d.actual_output, 2)" in prod, (
        "production actual output in kg should be formatted to 2 decimals"
    )
    assert not re.search(r"esc\(d\.actual_packets", prod), (
        "esc(d.actual_packets...) is still used in the production detail"
    )


def test_fmtnum_behaviour_matches_the_print_helper():
    """fmtNum must round like the existing num(), or the views will disagree."""
    fmt = re.search(
        r"function fmtNum\(v, dp\)\s*\{([\s\S]*?)\n\s*\}", _read("frontend/pages/challans.html")
    )
    assert fmt, "fmtNum not found in challans.html"
    body = fmt.group(1)
    assert "parseFloat" in body, "fmtNum must coerce the decimal string"
    assert "toFixed" in body, "fmtNum must use toFixed, exactly as num() does"
    assert "isNaN" in body, "fmtNum must not print NaN for a blank value"


# --------------------------------------------------------------------------
# 3. Auto-refresh must not read from a cached response
# --------------------------------------------------------------------------


def test_ui_api_disables_cache_for_reads():
    ui = _read("frontend/js/components.js")
    api = re.search(r"async function api\(path, opts = \{\}\)\s*\{([\s\S]*?)\n  \}", ui)
    assert api, "UI.api not found in components.js"
    body = api.group(1)
    assert '"no-store"' in body, (
        "UI.api must send cache: no-store on reads; otherwise auto-refresh can "
        "be handed a cached response and re-render the previous figures"
    )
    assert 'method === "GET"' in body, "no-store should be scoped to reads so writes are unaffected"
    # The cache option must land in the fetch options, not just be computed.
    assert "cache" in body.split("await fetch")[1], (
        "the computed cache value is never passed to fetch()"
    )


def test_api_responses_are_marked_uncacheable(main):
    """Drive the real middleware with a stub request."""

    async def call_next(_request):
        return types.SimpleNamespace(headers={})

    def run(path):
        req = types.SimpleNamespace(url=types.SimpleNamespace(path=path))
        resp = asyncio.run(main.no_store_for_api(req, call_next))
        return resp.headers.get("Cache-Control", "")

    for p in (
        "/dashboard/stats",
        "/sales/challans",
        "/settings/company",
        "/auth/me",
        "/production",
    ):
        assert "no-store" in run(p), f"{p} is cacheable: {run(p)!r}"


def test_every_api_route_prefix_is_uncacheable(main):
    """No endpoint may be cacheable by being forgotten in a prefix list.

    The finished-stock balance route was left out of an explicit list of API
    prefixes and so served without Cache-Control, reintroducing the stale
    auto-refresh bug on exactly the page that was meant to fix it. Enumerating
    the routes is the check; the middleware now exempts only static files, so
    this guards against that exemption list growing by accident.
    """
    src = _read("backend/main.py")

    async def call_next(_request):
        return types.SimpleNamespace(headers={})

    def cache_for(path):
        req = types.SimpleNamespace(url=types.SimpleNamespace(path=path))
        resp = asyncio.run(main.no_store_for_api(req, call_next))
        return resp.headers.get("Cache-Control", "")

    prefixes = set()
    for m in re.finditer(r'@app\.(?:get|post|put|patch|delete)\("([^"]+)"', src):
        p = m.group(1)
        if p == "/":
            continue
        prefixes.add("/" + p.strip("/").split("/")[0])

    # /share and /assets are deliberately static, alongside /frontend - the
    # pre-existing no_cache_frontend middleware treats them the same way.
    static_ok = {"/share", "/assets"}
    prefixes -= static_ok

    assert len(prefixes) > 20, f"only found {len(prefixes)} route prefixes"
    uncached = sorted(p for p in prefixes if "no-store" not in cache_for(p))
    assert not uncached, (
        "these route prefixes are cacheable: "
        + ", ".join(uncached)
        + " - the dashboard re-fetches them on a timer and would show stale data"
    )


def test_finished_stock_balance_route_is_uncacheable(main):
    """Named explicitly, because this is the one that slipped through."""

    async def call_next(_request):
        return types.SimpleNamespace(headers={})

    for p in ("/finished-stock", "/finished-stock/balance"):
        req = types.SimpleNamespace(url=types.SimpleNamespace(path=p))
        resp = asyncio.run(main.no_store_for_api(req, call_next))
        assert "no-store" in resp.headers.get("Cache-Control", ""), (
            f"{p} is cacheable; the finished-goods card would show stale balances"
        )


def test_frontend_files_keep_normal_caching(main):
    """Static files must not be forced no-store, or reloads get slower."""

    async def call_next(_request):
        return types.SimpleNamespace(headers={})

    req = types.SimpleNamespace(url=types.SimpleNamespace(path="/frontend/index.html"))
    resp = asyncio.run(main.no_store_for_api(req, call_next))
    assert "no-store" not in resp.headers.get("Cache-Control", "")


def test_auto_refresh_still_arms_its_timer():
    """The cache fix must not have disturbed the timer itself."""
    app = _read("frontend/js/app.js")
    assert re.search(r"AUTO_REFRESH_PAGES\s*=\s*\{\s*dashboard:\s*60000\s*\}", app), (
        "the dashboard should still auto-refresh every 60s"
    )
    assert "document.hidden" not in app.split("function autoRefreshPauseReason")[1][:900], (
        "autoRefreshPauseReason must not consult document.hidden, which the "
        "desktop WebView can report while the window is plainly visible"
    )
