"""Email recipient boxes tell the user commas are allowed.

The 553 blowup happened because the daily-report Recipients box gave no
hint that "a@x, b@y" is valid - the alert box already says it. Both
multi-address boxes must say so.

Run: python -m pytest backend/tests/test_email_recipient_hints.py -v
"""

import os


def _read():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "settings.html"), encoding="utf-8") as fh:
        return fh.read()


def test_all_recipient_boxes_advertise_comma_lists():
    html = _read()
    for field in ('id="em-alert-to"', 'id="rep-to"'):
        i = html.find(field)
        assert i >= 0, f"field {field} missing entirely"
        label_start = html.rfind("<label", 0, i)
        label = html[label_start:html.find("</label>", i) + len("</label>")]
        assert "comma separated" in label.lower(), (
            f"recipient box has no comma hint: {label.strip()}"
        )
