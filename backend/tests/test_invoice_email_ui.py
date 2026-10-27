"""Email-invoice-PDF action on the challan view.

Next to Download PDF sits Email PDF: blank sends to the customer email
on file, a typed address overrides for that send. Cancelling the prompt
sends nothing.

Run: python -m pytest backend/tests/test_invoice_email_ui.py -v
"""

import os


def _read():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "challans.html"), encoding="utf-8") as fh:
        return fh.read()


def test_email_pdf_button_exists():
    html = _read()
    assert "emailInvoicePdf" in html, "no Email PDF action on the challan view"
    assert "/send-email" in html, "Email PDF action never calls the send endpoint"


def test_cancelled_prompt_sends_nothing():
    html = _read()
    i = html.find("window.emailInvoicePdf")
    assert i >= 0
    body = html[i:i + 1500]
    assert "raw === null" in body and "return" in body, (
        "cancelling the address prompt must not send"
    )


def test_prompt_prefills_customer_email_on_file():
    html = _read()
    i = html.find("window.emailInvoicePdf")
    assert i >= 0
    body = html[i:i + 1500]
    assert "customer_email" in body, (
        "prompt opens blank even when the customer has an email on file"
    )
