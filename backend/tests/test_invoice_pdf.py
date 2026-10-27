"""Sales-invoice PDF download.

Run: python -m pytest backend/tests/test_invoice_pdf.py
"""

import os
import sys
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402
from backend.app.services import pdf_export as pe  # noqa: E402


def _req():
    return SimpleNamespace(headers={}, client=None)


def _inv_dict():
    return {
        "invoice_number": "INV/26-27/0001",
        "invoice_date": "2026-09-28T10:00:00",
        "challan_number": "CH/26-27/0001",
        "company_name": "Shresth Masala Industry",
        "company_address": "Bhagalpur",
        "company_phone": "+91 9430422515",
        "company_email": "",
        "company_gstin": "10BQYPJ6507J1ZK",
        "cust_company_name": "Payee Traders",
        "cust_address": "Market Road",
        "cust_gst_no": "10AAAAA0000A1Z5",
        "cust_contact_number": "9876500001",
        "place_of_supply": "Bihar",
        "intra_state": True,
        "items": [{
            "product_name": "Turmeric Powder", "size_name": "250g",
            "hsn_code": "09109100", "quantity": 100, "weight": 25.0,
            "rate_per_kg": 240, "amount": 6000.0, "taxable_value": 5714.29,
            "gst_rate": 5, "cgst": 142.86, "sgst": 142.86, "igst": 0,
        }],
        "taxable_value": 5714.29, "cgst": 142.86, "sgst": 142.86, "igst": 0,
        "grand_total": 6000.0,
        "total_in_words": "Rupees Six Thousand Only",
        "company_terms": "Goods once sold will not be taken back.",
    }


def _pdf_text(pdf: bytes) -> str:
    import io

    from pypdf import PdfReader

    raw = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages)
    return " ".join(raw.split())


def test_render_produces_pdf_with_invoice_figures():
    pdf = pe.render_invoice_pdf(_inv_dict())
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 2000
    text = _pdf_text(pdf)
    assert "INV/26-27/0001" in text
    assert "6,000.00" in text
    assert "Shresth Masala Industry" in text
    assert "28-Sep-2026" in text


def test_render_inter_state_uses_igst():
    d = _inv_dict()
    d["intra_state"] = False
    d["igst"] = 285.72
    pdf = pe.render_invoice_pdf(d)
    assert pdf[:4] == b"%PDF"
    assert "IGST" in _pdf_text(pdf)


def test_hindi_text_and_logo_survive():
    # Shaped Devanagari draws through glyph ids that text extractors cannot
    # map back, so "survive" here means what the eye sees: the PDF builds,
    # embeds the Nirmala subset for the Hindi runs, and keeps every figure.
    # (Before shaping, pypdf could read हल्दी while the printout showed
    # dotted circles - the worse side of the trade.)
    d = _inv_dict()
    d["company_name"] = "Shresth Masala Industry"
    d["items"][0]["product_name"] = "हल्दी पाउडर"
    d["cust_company_name"] = "पटेल प्रोविजन्स"
    pdf = pe.render_invoice_pdf(d)
    assert pdf[:4] == b"%PDF"
    assert b"Nirmala" in pdf
    text = _pdf_text(pdf)
    assert "6,000.00" in text
    assert pe._logo_path() is not None


def _db_invoice(db):
    c = models.Customer(customer_id="CUST-V1", name="PDF Traders",
                        contact_number="9000000003")
    db.add(c)
    prod = models.Product(product_id="P-V1", name="PDF Masala", gst_rate=Decimal("5"))
    db.add(prod)
    db.flush()
    size = models.PackagingSize(product_id=prod.id, size_name="250g",
                                weight_in_kg=Decimal("0.25"),
                                rate_per_kg=Decimal("240"), is_active=True)
    db.add(size)
    db.flush()
    ch = models.SalesChallan(
        challan_number="CH-V-1", challan_date=datetime(2026, 9, 28),
        customer_id=c.id, status="confirmed", total_price=Decimal("6000"),
        paid_amount=Decimal("0"), outstanding_amount=Decimal("6000"))
    db.add(ch)
    db.flush()
    inv = models.SalesInvoice(
        invoice_number="INV-V-1", invoice_date=datetime(2026, 9, 28),
        challan_id=ch.id, customer_id=c.id,
        company_name="Shresth Masala Industry", company_gstin="10BQYPJ6507J1ZK",
        cust_company_name="PDF Traders", intra_state=True,
        taxable_value=Decimal("5714.29"), cgst=Decimal("142.86"),
        sgst=Decimal("142.86"), igst=Decimal("0"),
        grand_total=Decimal("6000"))
    db.add(inv)
    db.flush()
    db.add(models.SalesInvoiceItem(
        invoice_id=inv.id, product_id=prod.id, packaging_size_id=size.id,
        quantity=Decimal("100"), weight=Decimal("25"), rate_per_kg=Decimal("240"),
        amount=Decimal("6000"), taxable_value=Decimal("5714.29"), gst_rate=Decimal("5"),
        cgst=Decimal("142.86"), sgst=Decimal("142.86"), igst=Decimal("0"),
        product_name="PDF Masala", size_name="250g", hsn_code="09109100"))
    db.commit()
    return inv


def test_endpoint_returns_pdf_for_real_invoice(db):
    import asyncio

    inv = _db_invoice(db)
    res = m.download_invoice_pdf(inv.id, db)
    assert res.media_type == "application/pdf"
    assert "attachment" in res.headers["content-disposition"]

    async def _collect():
        return b"".join([chunk async for chunk in res.body_iterator])

    body = asyncio.run(_collect())
    assert body[:4] == b"%PDF"
    assert "INV-V-1" in _pdf_text(body)


def test_endpoint_404_for_unknown_invoice(db):
    with pytest.raises(HTTPException) as e:
        m.download_invoice_pdf(999999, db)
    assert e.value.status_code == 404


def test_mixed_rates_get_one_row_set_each():
    d = _inv_dict()
    d["items"] = [dict(d["items"][0], gst_rate=5, cgst=100, sgst=100, igst=0),
                  dict(d["items"][0], gst_rate=12, cgst=120, sgst=120, igst=0)]
    text = _pdf_text(pe.render_invoice_pdf(d))
    assert "CGST @ 2.5%" in text
    assert "CGST @ 6%" in text
    assert "SGST @ 6%" in text


def test_invoice_list_carries_customer_and_challan(db):
    _db_invoice(db)
    rows = m.list_invoices(db=db)
    assert len(rows) == 1
    row = rows[0]
    assert row["invoice_number"] == "INV-V-1"
    assert row["customer_name"] == "PDF Traders"
    assert row["challan_number"] == "CH-V-1"
    assert "items" not in row
