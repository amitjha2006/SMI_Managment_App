"""Invoice PDFs stay small: the logo is downscaled before embedding.

The letterhead logo ships at ~1.5MB and was embedded whole, so every
invoice PDF ran to ~2.6MB and stalled slow SMTP uploads. Rendered at
38x30mm it needs only a thumbnail: the PDF must stay a small file
while keeping every figure.

Run: python -m pytest backend/tests/test_invoice_pdf_slim.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app.services import pdf_export as pe  # noqa: E402
from backend.tests.test_invoice_pdf import _inv_dict, _pdf_text  # noqa: E402


def test_invoice_pdf_stays_small_with_real_logo():
    pdf = pe.render_invoice_pdf(_inv_dict())
    assert pdf[:4] == b"%PDF"
    assert len(pdf) < 500_000, f"invoice PDF is {len(pdf):,} bytes (logo embedded whole?)"
    text = _pdf_text(pdf)
    assert "INV/26-27/0001" in text, "slimming dropped invoice content"
    assert "6,000.00" in text, "slimming dropped invoice content"
