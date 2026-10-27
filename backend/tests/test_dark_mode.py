"""Dark-mode readability across every page.

Light surfaces hardcoded in markup/JS (detail rows, dispatch boxes,
empty-state text, suggest hover, chart grid) must stay readable when
data-theme="dark" is on. Light mode itself is untouched: every dark rule
is scoped under [data-theme="dark"], and print always renders light.

Run: python -m pytest backend/tests/test_dark_mode.py -v
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _read(relpath):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *relpath.split("/")), encoding="utf-8") as fh:
        return fh.read()


def _dark_css():
    css = _read("frontend/css/style.css")
    i = css.find('[data-theme="dark"]')
    assert i >= 0, "no dark theme block"
    return css[i:]


# Every light hex rendered inline by page JS needs a dark-side rule.
INLINE_LIGHT = [
    "#f5f8fc",  # detail rows, guide boxes, suggest hover, dispatch draft
    "#fef9c3",  # payment-row highlight
    "#d1fae5",  # delivered box
    "#fef3c7",  # dispatched box
    "#f1f5f9",  # archived box
    "#5b6b84",  # muted empty-state text
    "#101a2e",  # ink text
    "#a7f3d0",  # BAL-cell hover
]


def test_dark_rules_cover_inline_light_surfaces():
    dark = _dark_css().lower()
    for hx in INLINE_LIGHT:
        assert hx in dark, f"no dark rule for {hx}"


def test_chart_grid_and_labels_follow_theme():
    dash = _read("frontend/pages/dashboard.html")
    assert "#28365a" in dash, "chart grid stays light in dark mode"
    assert "#9db0c7" in dash, "chart labels stay muted-gray in dark mode"


def test_light_mode_untouched():
    css = _read("frontend/css/style.css")
    light = css[:css.find('[data-theme="dark"]')]
    assert "--color-bg: #e9eef5" in light, "light background changed"
    assert "--color-surface: #ffffff" in light, "light surface changed"


def test_print_stays_light():
    app_js = _read("frontend/js/app.js")
    assert "beforeprint" in app_js, "print no longer forces light theme"


def test_header_bar_and_search_readable_in_dark():
    css = _read("frontend/css/style.css")
    dark = css[css.find('[data-theme="dark"]'):]
    assert ".top-header" in dark, "header bar stays white in dark mode"
    assert "global-search" in dark, "search box has no dark styling"


def test_header_is_a_visible_bar_in_both_themes():
    """The header was a translucent white bar over a near-white page, which
    users reported as "almost invisible". It must stay a solid, lifted
    surface with an accent underline in light AND a lighter-than-page
    surface in dark."""
    css = _read("frontend/css/style.css")
    light = css[: css.find('[data-theme="dark"]')]
    assert "background: #ffffff;\n  backdrop-filter" in light, "light header is not solid white"
    assert "bottom: -1px;\n  height: 3px;" in light, "header accent underline missing"
    dark = css[css.find('[data-theme="dark"]'):]
    assert "background: #182338;" in dark, "dark header has no distinct surface"


def test_status_pills_readable_in_dark():
    css = _read("frontend/css/style.css")
    dark = css[css.find('[data-theme="dark"]'):]
    for pill in (".badge-danger", ".badge-warning", ".badge-success"):
        assert pill in dark, f"{pill} text unreadable on dark"


def test_editor_toggle_readable_in_dark():
    css = _read("frontend/css/style.css")
    dark = css[css.find('[data-theme="dark"]'):]
    assert ".editor-toggle" in dark, "editor toggle unreadable in dark mode"


def test_dark_mode_keeps_analysis_pills():
    css = _read("frontend/css/style.css")
    dark = css[css.find('[data-theme="dark"]'):]
    assert "pill-sale" in dark, "dark header paint covers the Sale pill"


def test_row_action_colors_are_semantic():
    """View=blue, Payment/Pay=green, Edit=amber, Delete=red. The color says
    the action before the label is read; light rules live in the light
    section, dark overrides scoped under [data-theme]."""
    css = _read("frontend/css/style.css")
    # Dark-scoped rules exist inside the light section, so the real light/dark
    # boundary is the dark theme-variable block, not the first dark mention.
    light = css[: css.find('[data-theme="dark"] {')]
    for cls, hx in (
        (".btn-info", "#0369a1"),
        (".btn-success", "#047857"),
        (".btn-warn", "#b45309"),
    ):
        assert cls in light, f"{cls} missing in light section"
        assert hx in light, f"{cls} lost its semantic hue {hx}"
    dark = _dark_css()
    for cls in (".btn-info", ".btn-success", ".btn-warn"):
        assert f'[data-theme="dark"] {cls}' in dark, f"{cls} has no dark variant"


def test_view_payment_edit_use_semantic_colors():
    """Challans and invoices rows: View must not share Payment's color and
    Edit must not be a plain white button."""
    ch = _read("frontend/pages/challans.html")
    assert re.search(r"btn-info" + r'"[^>]*>View<', ch), "challan View is not blue"
    # The payment button spells out what it does ("₹ Payment Received") —
    # it still must be green so money actions keep their semantic color.
    assert re.search(r"btn-success" + r'"[^>]*>₹ Payment Received<', ch), (
        "challan Payment Received is not green"
    )
    assert re.search(r"btn-warn" + r'"[^>]*>Edit<', ch), "challan Edit is not amber"
    inv = _read("frontend/pages/invoices.html")
    assert re.search(r"btn-info" + r'"[^>]*>View<', inv), "invoice View is not blue"
    assert re.search(r"btn-success" + r'"[^>]*>Pay<', inv), "invoice Pay is not green"


def test_view_button_visible_to_viewer_role():
    """role-viewer hides .btn-primary in cards; View must use a class that
    is exempt so read-only users keep their View buttons."""
    import re as _re
    for page in ("customers.html", "suppliers.html", "employees.html",
                 "invoices.html", "production.html", "audit.html"):
        src = _read("frontend/pages/" + page)
        assert _re.search(r"btn-info" + r'"[^>]*>(View|>)', src) or "btn-info" in src, \
            f"{page} View is not btn-info (viewer would lose it)"


def test_tool_and_wa_button_colors_pinned():
    """Tool buttons (Help/History/Copy/Email PDF) carry a violet accent and
    WhatsApp shares a brand green; both shipped as mid-session additions, so
    pin light + dark variants like the other semantic colors."""
    css = _read("frontend/css/style.css")
    light = css[: css.find('[data-theme="dark"] {')]
    for cls, hx in ((".btn-tool", "#6d28d9"), (".btn-wa", "#16a34a")):
        assert cls in light, f"{cls} missing in light section"
        assert hx in light, f"{cls} lost its hue {hx}"
    dark = _dark_css()
    for cls in ("btn-tool", "btn-wa"):
        assert f'[data-theme="dark"] .{cls}' in dark, f".{cls} has no dark variant"
    inv = _read("frontend/pages/inventory.html")
    # Help + Copy-recipe (+ dark-mode pin test below asserts Opening/Workflow
    # moved to their own hues) — at least two tool buttons remain violet.
    assert inv.count("btn-tool") >= 2, "product-row tool buttons lost their accent"
    for page in ("customers.html", "followup.html", "challans.html"):
        assert "btn-wa" in _read("frontend/pages/" + page), \
            f"{page} WhatsApp share is not brand-green"


def test_analysis_groups_header_ink_matches_wash():
    """The groups skin repaints header cells to light washes; without its own
    ink color the base white text sits on a near-white wash and vanishes."""
    an = _read("frontend/pages/analysis.html")
    assert "thead th.grp-a { background: #e3ebf5; color: #102a43; }" in an, \
        "groups header keeps white-on-white in light mode"
    assert "thead th.grp-b { background: #f3ecdc; color: #5c4a1e; }" in an, \
        "groups header grp-b ink missing"
    dark = an[an.find('[data-theme="dark"] body[data-anstyle="groups"]'):]
    assert "color: #e7eef8;" in dark, "groups dark headers rely on inherited color"


def test_extra_row_action_hues_pinned():
    """Opening=fuchsia, Workflow=indigo, Restore-family=slate, Export/PDF/
    Print=navy — four more hues so differently-named row buttons never share
    one color. Light + dark, like every other semantic button color."""
    css = _read("frontend/css/style.css")
    light = css[: css.find('[data-theme="dark"] {')]
    for cls, hx in (
        (".btn-opening", "#a21caf"),
        (".btn-workflow", "#4338ca"),
        (".btn-restore", "#475569"),
        (".btn-export", "#1e40af"),
    ):
        assert cls in light, f"{cls} missing in light section"
        assert hx in light, f"{cls} lost its hue {hx}"
    dark = _dark_css()
    for cls in ("btn-opening", "btn-workflow", "btn-restore", "btn-export"):
        assert f'[data-theme="dark"] .{cls}' in dark, f".{cls} has no dark variant"
    inv = _read("frontend/pages/inventory.html")
    assert "btn-opening" in inv and "btn-workflow" in inv, \
        "product row Opening/Workflow lost their distinct hues"


def test_unpaid_only_checkbox_reaches_its_handler():
    """The unpaid-only checkbox once called IIFE-private saveChallanFilters,
    reference which throws from inline handlers and left the filter dead
    (paid challans kept showing in 'Unpaid only'). The onchange must call a
    window-bound wrapper."""
    ch = _read("frontend/pages/challans.html")
    assert 'onchange="challanUnpaidChanged()"' in ch, "checkbox not on a global handler"
    assert "window.challanUnpaidChanged" in ch, "handler is not window-bound"
    assert 'onchange="saveChallanFilters(); filterChallans();"' not in ch, \
        "the crashing inline handler is still there"


def test_restore_and_export_button_classes_in_use():
    """Restore-family buttons (slate) and export/print buttons (navy) must
    actually carry their classes app-wide."""
    for page in ("challans.html", "expenses.html", "invoices.html", "production.html"):
        src = _read("frontend/pages/" + page)
        assert "btn-restore" in src, f"{page} Restore button is not slate"
    for page in ("backup.html", "reports.html", "challans.html"):
        src = _read("frontend/pages/" + page)
        assert "btn-export" in src, f"{page} download/print buttons are not navy"
    users = _read("frontend/pages/users.html")
    assert "btn-tool" in users, "users Reset PW lost its accent"
