"""Dues follow-up page: call list with WhatsApp reminders.

The page loads the follow-up endpoint, renders one row per owing
customer (total due formatted as money), and offers a WhatsApp button
that opens wa.me with the dues text prefilled. Phone numbers normalize
to 91-prefix; unusable numbers get an explanatory disabled button,
never a dead one.

Run: python -m pytest backend/tests/test_dues_followup_ui.py -v
"""

import os


def _read(rel):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_followup_page_is_routed():
    app = _read("frontend/js/app.js")
    assert "/followup" in app and "followup.html" in app, (
        "follow-up page is not registered in the router"
    )
    index = _read("frontend/index.html")
    assert "#/followup" in index, "follow-up page has no sidebar entry"


def test_followup_page_loads_the_endpoint():
    html = _read("frontend/pages/followup.html")
    assert "/reports/dues-followup" in html, "page never calls the follow-up endpoint"
    assert "UI.money" in html, "due amounts show as bare numbers"


def test_followup_page_reminds_over_whatsapp():
    html = _read("frontend/pages/followup.html")
    assert "wa.me" in html, "no WhatsApp reminder action"
    assert "91" in html, "phone numbers are not normalized to 91-prefix"
