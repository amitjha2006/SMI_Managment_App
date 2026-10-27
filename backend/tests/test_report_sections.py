"""Daily report section selection (Automatic Daily Report Mail).

The Settings card offers one checkbox per report section with a plain-word
label. Unset/empty configuration defaults to every known section (the same
default the settings endpoint serves), an empty selection is refused at
save time, and the weekday picker only applies to weekly mails.

Run: python -m pytest backend/tests/test_report_sections.py -v
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import backend.main as m  # noqa: E402


def _req():
    return SimpleNamespace(query_params={"reason": "test change note"}, headers={}, client=None)


def _read_settings_html():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "settings.html"), encoding="utf-8") as fh:
        return fh.read()


def _read_reports_html():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "reports.html"), encoding="utf-8") as fh:
        return fh.read()


def test_reports_print_hides_chrome():
    # window.print() prints the whole DOM, so the Reports page must hide the
    # app shell (sidebar, top header with the Editor Mode toggle) and the
    # builder controls; otherwise the printout carries them all.
    html = _read_reports_html()
    i = html.find("@media print")
    assert i >= 0, "reports page has no print rules"
    body = html[i:i + 1500]
    for needle in (".sidebar", ".top-header", ".btn"):
        assert needle in body, f"print rules leave {needle} visible"


def test_default_build_includes_dues(db):
    _subject, text, _html = m._build_daily_report(db)
    assert "Dues follow-up" in text, text


def test_empty_sections_fall_back_to_full_default(db):
    _subject, text, _html = m._build_daily_report(db, [])
    assert "Dues follow-up" in text, text


def test_default_matches_settings_endpoint_default(db):
    _subject, text, _html = m._build_daily_report(db)
    cfg = m.get_email_settings(db)
    for sec in cfg["report_sections"]:
        assert sec in m.REPORT_SECTION_CHOICES, sec
    assert "Dues follow-up" in text, text


def test_empty_sections_refused_at_save(db):
    with pytest.raises(HTTPException) as e:
        m.update_email_settings({"report_sections": []}, _req(), db)
    assert e.value.status_code == 400


def test_unknown_section_refused_at_save(db):
    with pytest.raises(HTTPException) as e:
        m.update_email_settings({"report_sections": ["stock", "nope"]}, _req(), db)
    assert e.value.status_code == 400


def test_sections_round_trip_through_settings(db):
    m.update_email_settings({"report_sections": ["stock", "dues"]}, _req(), db)
    cfg = m.get_email_settings(db)
    assert cfg["report_sections"] == ["stock", "dues"], cfg


def test_boxes_use_plain_word_labels():
    html = _read_settings_html()
    for label in (
        "Stock summary",
        "Sales this month",
        "Unpaid supplier bills",
        "Money owed to us / by us",
        "Low-stock items",
        "Dues follow-up list",
    ):
        assert label in html, f"section label missing: {label}"


def test_boxes_offer_select_all_and_clear():
    html = _read_settings_html()
    assert "setAllSections" in html, "no select-all/clear helper for the section boxes"
    assert "Select all" in html and "Clear" in html, "select-all/clear links missing"


def test_weekday_picker_gated_on_weekly():
    html = _read_settings_html()
    assert "rep-weekday-wrap" in html, "weekday row has no wrapper to hide for daily"
    assert "syncFrequencyVisibility" in html, "no weekday visibility toggle"


def test_empty_selection_blocked_before_save():
    html = _read_settings_html()
    assert "Pick at least one section" in html, (
        "unchecking every box still fails only as a server 400"
    )


def test_recipients_box_gets_full_width_row():
    html = _read_settings_html()
    card = html[html.find('id="report-settings-card"'):html.find('id="shares-card"')]
    assert card.count('class="form-row"') >= 2, (
        "recipients shares one cramped row with the schedule controls"
    )
    assert 'id="rep-to"' in card
