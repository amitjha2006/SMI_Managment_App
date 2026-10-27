"""WhatsApp reminder text is plain text, never display HTML.

The message is opened through wa.me, so it must read like a message, not
markup. The display helper dateWithTime returns "day<br><small>time</small>"
for table cells - interpolating it into the reminder text leaked raw HTML
into the chat ("...oldest due 6/5/2026<br><small ...>10:00 AM IST</small>").
The message therefore formats its date with the plain-text helper.

Run: python -m pytest backend/tests/test_followup_message.py -v
"""

import os
import re


def _read(rel):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_plain_text_date_helper_exists():
    js = _read("frontend/js/components.js")
    assert re.search(r"function dateTimeText\(", js), (
        "no plain-text date helper for messages"
    )
    assert "dateTimeText" in js.split("return {")[-1], (
        "plain-text date helper not exported"
    )


def test_whatsapp_text_uses_plain_date_not_cell_html():
    html = _read("frontend/pages/followup.html")
    i = html.find("wa.me")
    assert i >= 0
    window = html[max(0, i - 1200):i]
    assert "dateTimeText" in window, (
        "reminder text does not use the plain-text date helper"
    )
    assert "dateWithTime" not in window, (
        "reminder text interpolates table-cell HTML into the chat message"
    )
