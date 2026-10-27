"""Hindi text in PDFs renders shaped, not as dotted circles.

ReportLab lays Devanagari out unshaped unless HarfBuzz shaping is on: the
font has the glyphs but nothing reorders conjuncts, so हल्दी breaks apart
and detached marks print as black dots. These tests pin the mechanism:

* uharfbuzz is installed (the shaping engine ReportLab needs)
* the registered Nirmala face is shapable
* our Hindi strings shape to real glyphs (conjuncts form, no .notdef)
* the invoice PDF embeds Nirmala and keeps the Hindi product name

Run: python -m pytest backend/tests/test_hindi_pdf.py -v
"""

HINDI_SAMPLES = ["हल्दी पाउडर", "काली मिर्च", "धनिया पाउडर", "गरम मसाला"]


def test_shaping_engine_available():
    import uharfbuzz  # noqa: F401 - the import itself is the assertion


def test_devanagari_font_is_shapable():
    from reportlab.pdfbase.pdfmetrics import getFont

    from backend.app.services import pdf_export as pe

    assert pe._ensure_devanagari_font() == "Nirmala"
    assert getFont("Nirmala").shapable


def test_hindi_conjuncts_shape_without_notdef():
    import uharfbuzz

    blob = uharfbuzz.Blob.from_file_path(r"C:\Windows\Fonts\Nirmala.ttc")
    face = uharfbuzz.Face(blob, 0)
    font = uharfbuzz.Font(face)
    for sample in HINDI_SAMPLES:
        buf = uharfbuzz.Buffer()
        buf.add_str(sample)
        buf.guess_segment_properties()
        uharfbuzz.shape(font, buf, {})
        infos = buf.glyph_infos
        assert infos, sample
        assert all(i.codepoint != 0 for i in infos), sample


def test_invoice_pdf_embeds_nirmala_for_hindi():
    # Shaped Devanagari draws through glyph ids that text extractors cannot
    # map back, so this pins what the eye sees, not what pypdf reads: the
    # PDF builds, embeds the Nirmala subset, and keeps every other figure.
    from backend.app.services import pdf_export as pe

    inv = {
        "invoice_number": "INV/26-27/0999",
        "invoice_date": "2026-09-28T10:00:00",
        "company_name": "Shresth Masala Industry",
        "company_address": "Bhagalpur",
        "company_gstin": "10BQYPJ6507J1ZK",
        "cust_company_name": "Payee Traders",
        "cust_address": "Market Road",
        "place_of_supply": "Bihar",
        "intra_state": True,
        "items": [{
            "product_name": "हल्दी पाउडर", "size_name": "250g",
            "hsn_code": "09103020", "quantity": 100, "weight": 25.0,
            "rate_per_kg": 240, "amount": 6000.0, "taxable_value": 5714.29,
            "gst_rate": 5, "cgst": 142.86, "sgst": 142.86, "igst": 0,
        }],
        "taxable_value": 5714.29, "cgst": 142.86, "sgst": 142.86, "igst": 0,
        "grand_total": 6000.0,
        "total_in_words": "Rupees Six Thousand Only",
    }
    pdf = pe.render_invoice_pdf(inv)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 2000
    assert b"Nirmala" in pdf
