"""Dashboard UX batch: hideable summary rows, taller analysis, date nowrap,
custom snapshot list.

1. Raw Material Summary lets you uncheck materials so they leave the table;
   the choice persists per browser and the stat cards + Total row follow
   the visible rows only.
2. Analysis shows more rows (taller scroll area + live counter) with a
   stronger table header.
3. The dashboard date never wraps and keeps padding from the buttons.
4. The Inventory Snapshot list is yours: count + pinned materials in the
   Customize modal, defaulting to today's top-8.

Run: python -m pytest backend/tests/test_dashboard_ux4.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _read(relpath):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *relpath.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_summary_rows_can_be_hidden_and_persisted():
    html = _read("frontend/pages/raw-material-summary.html")
    assert "rmsum-search" in html, "no summary search box"
    assert "rmsum_hidden" in html, "hidden choice is not persisted"
    assert "rmsum-show-selected" in html, "no show-selected control"
    assert "rmsum-reset" in html, "no reset control"


def test_summary_totals_follow_visible_rows():
    html = _read("frontend/pages/raw-material-summary.html")
    assert "visibleRows" in html or "visible rows" in html, (
        "totals still cover hidden rows"
    )


def test_analysis_shows_more_with_counter_and_header():
    html = _read("frontend/pages/analysis.html")
    assert "an-count" in html, "no live product counter"
    # The user asked the table shorter so the footer stays reachable; 65vh
    # keeps a long scroll without the page running the card off-screen.
    assert "65vh" in html, "scroll area lost its height cap"
    i = html.find(".analysis-table thead th")
    assert i >= 0
    block = html[i:i + 400]
    assert "13px" in block and "600" in block, "header still pale/small"


def test_analysis_header_sticks_as_one_unit():
    html = _read("frontend/pages/analysis.html")
    assert "an-head-table" in html, "no separate header table"
    assert "an-body-table" in html, "no separate body table"
    assert "scrollLeft" in html, "header/body horizontal scroll not synced"
    assert "top: 40px" not in html and "top: 41px" not in html, (
        "fragile per-row offsets still present"
    )


def test_width_sync_measures_matching_columns():
    html = _read("frontend/pages/analysis.html")
    i = html.find("function syncAnWidths")
    assert i >= 0
    block = html[i:i + 2500]
    # Pills row pairs 1:1 with body cells; group cells (colspan) derive
    # theirs. Measuring all thead cells can never match one body row.
    assert "nth-child(2)" in block, "sync still measures every header row"


def test_scroll_sync_runs_both_ways():
    html = _read("frontend/pages/analysis.html")
    i = html.find("bindAnScrollSync")
    assert i >= 0
    block = html[i:i + 900]
    assert "head.scrollLeft = body.scrollLeft" in block, "body scroll lost"
    assert "body.scrollLeft = head.scrollLeft" in block, "header scroll lost"


def test_group_labels_centered_over_trio():
    html = _read("frontend/pages/analysis.html")
    i = html.find("thead tr:first-child th[colspan]")
    assert i >= 0, "group headers not centered: empty cell illusion"
    assert "center" in html[i:i + 120], "group headers left-aligned"


def test_corner_header_outranks_body_text():
    html = _read("frontend/pages/analysis.html")
    i = html.find("#an-head-table thead th.col-product")
    assert i >= 0, "corner header has no authority styling"
    block = html[i:i + 160]
    assert "800" in block, "corner header not bolder than body names"


def test_analysis_top_boxes_are_compact():
    html = _read("frontend/pages/analysis.html")
    assert "an-compact" in html, "filter/summary boxes still tall"
    i = html.find(".an-stats .stat-card .stat-value")
    assert i >= 0 and "16px" in html[i:i + 120], "summary values still large"


def test_analysis_style_experiment():
    html = _read("frontend/pages/analysis.html")
    assert 'id="an-style"' in html, "no style switcher"
    assert "Bold" in html and "Classic" in html, "switcher misses an option"
    assert "an_style" in html, "choice is not persisted"
    assert "data-anstyle" in html, "style never applied"
    assert 'body[data-anstyle="ledger"] #an-head-table' in html, (
        "no ledger skin scoped to this table"
    )
    assert '[data-theme="dark"] body[data-anstyle="ledger"]' in html, (
        "no dark ledger skin"
    )


def test_analysis_style_groups():
    html = _read("frontend/pages/analysis.html")
    assert 'value="groups"' in html, "no Groups option in the switcher"
    assert "grp-a" in html and "grp-b" in html, "no per-group wash classes"
    i = html.find('[data-anstyle="groups"]')
    assert i >= 0, "no groups skin"
    assert ":not(.neg)" in html[i:i + 2500], "wash covers negatives/BAL"
    assert '[data-theme="dark"] body[data-anstyle="groups"]' in html, (
        "no dark groups skin"
    )


def test_analysis_style_sparks():
    html = _read("frontend/pages/analysis.html")
    assert 'value="sparks"' in html, "no Sparkbars option in the switcher"
    i = html.find("ANSTYLES = ")
    assert i >= 0 and "sparks" in html[i:i + 120], "sparks not selectable"
    assert "spark-cell" in html and "spark-fill" in html, "no bar markup"
    assert '[data-anstyle="sparks"]' in html, "no sparks skin"
    assert '[data-theme="dark"] body[data-anstyle="sparks"]' in html, (
        "no dark sparks skin"
    )


def test_analysis_style_heatmap():
    html = _read("frontend/pages/analysis.html")
    assert 'value="heat"' in html, "no Heatmap option in the switcher"
    i = html.find("ANSTYLES = ")
    assert i >= 0 and "heat" in html[i:i + 120], "heat not selectable"
    assert "heat-cell" in html, "no heat markup"
    assert "maxAbsBal" in html, "no magnitude scaling"


def test_analysis_style_ink():
    html = _read("frontend/pages/analysis.html")
    assert 'value="ink"' in html, "no Data-ink option in the switcher"
    i = html.find("ANSTYLES = ")
    assert i >= 0 and "ink" in html[i:i + 140], "ink not selectable"
    assert "ink-neg" in html and "ink-box" in html, "no ink markup"
    assert 'body[data-anstyle="ink"]' in html, "no ink skin"
    assert "pill-stock" in html, "pills row missing"


def test_ink_negatives_stay_red_and_bold():
    css = _read("frontend/pages/analysis.html")
    j = css.find("td.ink-neg")
    assert j >= 0, "no negatives rule"
    block = css[j:j + 220]
    assert "#be123c" in block and "700" in block, "negatives not red bold"
    k = css.find("td.ink-neg.col-bal", j)
    assert k >= 0, "green BAL rule still beats red negatives"


def test_ink_bal_chip():
    html = _read("frontend/pages/analysis.html")
    i = html.find("td.ink-nz.col-bal")
    assert i >= 0, "no Bal chip rule"
    assert "#d1fae5" in html[i:i + 220], "chip has no fill"


def test_ink_boxes_for_stock_and_sale():
    html = _read("frontend/pages/analysis.html")
    assert "ink-stock" in html and "ink-sale" in html, "no per-column boxes"
    i = html.find(".ink-box.ink-stock")
    assert i >= 0 and "#0369a1" in html[i:i + 160], "stock box not blue"
    j = html.find(".ink-box.ink-sale")
    assert j >= 0 and "#b45309" in html[j:j + 160], "sale box not amber"


def test_boxes_show_in_every_style():
    html = _read("frontend/pages/analysis.html")
    i = html.find("#an-body-table .ink-box.ink-stock")
    assert i >= 0, "boxes gated to one style"
    assert "#an-body-table .ink-box.ink-sale" in html, "sale boxes gated"
    assert "td.ink-nz.col-bal:not(.heat-cell)" in html, "BAL green fights heat"


def test_dashboard_date_never_wraps():
    html = _read("frontend/pages/dashboard.html")
    i = html.find('id="dash-date"')
    assert i >= 0
    block = html[i:i + 200]
    assert "nowrap" in block, "date can still wrap"


def test_snapshot_list_is_customizable():
    html = _read("frontend/pages/dashboard.html")
    assert "snap-raw-count" in html, "no snapshot count control"
    assert "dash_snap_raw" in html, "snapshot choice is not persisted"
    assert "slice(0, 8)" in html, "default top-8 changed"


def test_snapshot_list_is_sortable():
    html = _read("frontend/pages/dashboard.html")
    assert "snap-raw-sort" in html, "no snapshot sort control"
    assert "snapSort" in html or "snap-sort" in html, "sort choice is not applied"


def test_dashboard_kpi_boxes_are_compact():
    html = _read("frontend/pages/dashboard.html")
    assert ".dash-compact .stat-card { padding: 10px 14px; }" in html, (
        "KPI boxes still tall"
    )
    assert ".dash-compact .stat-card .stat-value { font-size: 20px; }" in html, (
        "KPI values still large"
    )
