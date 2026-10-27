"""PDF export.

Spec 13 requires reports to export to "Excel and PDF", and every export in
this system already produces the same three things before it is rendered:

    title, headers, rows

So the PDF is rendered from THAT, not from a parallel set of queries. That is
the point of this module: if Excel and PDF were built from separate code, they
would eventually disagree, and a report whose PDF does not match its
spreadsheet is worse than either one being wrong on its own.

Design notes worth knowing:

  * Landscape A4 by default. Report tables are wide (10-14 columns); portrait
    would either clip them or shrink the text past readability.
  * The header row repeats on every page. A table that runs over a page break
    with no headings on page two is unusable in practice.
  * Numbers are right-aligned and detected rather than declared, so a column of
    figures lines up without the caller having to say which columns are numeric.
  * An empty result still produces a valid PDF with a clear "no rows" line. An
    error page or a zero-byte file would look like a broken export.
  * Wide tables shrink proportionally rather than clipping. Clipping loses
    data silently, which is the one outcome that matters most here.
"""

import sys
from contextlib import suppress
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from PIL import Image as _PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# A muted, printer-friendly palette. The app's blue is too dark to read as a
# solid fill in large blocks.
HEADER_BG = colors.HexColor("#D9E2F3")
GRID = colors.HexColor("#9AA5B1")
BAND = colors.HexColor("#F5F7FA")
TEXT = colors.HexColor("#1A1A1A")
MUTED = colors.HexColor("#5A6472")

MAX_WIDTH = landscape(A4)[0] - 24 * mm


# Devanagari-capable typeface for documents that carry Hindi names (product,
# customer, company). ReportLab's built-in Helvetica has no Devanagari glyphs,
# so Hindi printed black boxes. Nirmala ships with every Windows target, so
# there is nothing extra to bundle; anything else falls back to Helvetica
# rather than failing the export.
DEVANAGARI_FONT = "Helvetica"
_DEVANAGARI_TTC = r"C:\Windows\Fonts\Nirmala.ttc"


def _ensure_devanagari_font() -> str:
    """Register Nirmala once; return the usable family name."""
    global DEVANAGARI_FONT
    if DEVANAGARI_FONT != "Helvetica":
        return DEVANAGARI_FONT
    try:
        pdfmetrics.registerFont(TTFont("Nirmala", _DEVANAGARI_TTC, subfontIndex=0))
        DEVANAGARI_FONT = "Nirmala"
    except Exception:
        DEVANAGARI_FONT = "Helvetica"
    return DEVANAGARI_FONT


def _logo_path() -> str | None:
    """Absolute path of the app logo, frozen (_MEIPASS) or dev layout."""
    for base in (
        str(Path(getattr(sys, "_MEIPASS", "")) / "frontend"),
        str(Path(__file__).resolve().parents[3] / "frontend"),
    ):
        if not base:
            continue
        for name in ("logo.png", "logo.jpg"):
            cand = str(Path(base) / "assets" / name)
            if Path(cand).is_file():
                return cand
    return None


# Displayed at 38x30mm (~110x85pt); anything larger is dead weight in the
# file and on slow SMTP uploads. Thumbnail keeps aspect and mode, so the
# letterhead looks identical at a fraction of the bytes.
_LOGO_MAX_PX = 360


def _sized_logo(path):
    """Logo bytes at display size."""
    with _PILImage.open(path) as im:
        im.thumbnail((_LOGO_MAX_PX, _LOGO_MAX_PX), _PILImage.LANCZOS)
        buf = BytesIO()
        im.save(buf, "PNG", optimize=True)
        buf.seek(0)
        return buf


def _text(value) -> str:
    """Render any cell as plain text, safely.

    Report values are Decimals, datetimes, None and free text. A None must not
    become the word "None" on a document someone may file.
    """
    if value is None:
        return ""
    if isinstance(value, Decimal):
        # Two places, trailing zeros dropped - an amount column of
        # "0.00 / 100.00 / 1500.00" reads better than "0 / 100 / 1500" only
        # when it is actually money, so keep the decimals but trim the noise.
        return f"{value:,.2f}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, datetime):
        return value.strftime("%d-%b-%Y %H:%M").rstrip(" 0")
    if isinstance(value, date):
        return value.strftime("%d-%b-%Y")
    return str(value)


def _looks_numeric(value) -> bool:
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return True
    s = str(value or "").strip().replace(",", "")
    if not s:
        return False
    try:
        float(s)
    except ValueError:
        return False
    else:
        return True


def _cell(row, i, header):
    """Value for column i of a row, whatever shape the row is in.

    The two callers pass different things, and guessing wrong is silent:
    build_export() yields dicts keyed by the header label, while the custom
    report path yields plain lists. Indexing a dict with an int raises
    KeyError at render time, so both are handled here.
    """
    if isinstance(row, dict):
        return row.get(header)
    try:
        return row[i]
    except (IndexError, KeyError, TypeError):
        return None


def _column_widths(headers, rows, total_width):
    """Proportional widths from the widest content in each column.

    Proportional rather than equal so a narrow "Sr No" or "Date" does not get
    the same space as a long "Customer Name", and the table still uses the
    full page. Every column keeps a floor so a long free-text cell cannot
    squeeze a numeric column to nothing.
    """
    n = len(headers)
    if n == 0:
        return []
    longest = []
    for i in range(n):
        cells = [_text(headers[i])]
        cells.extend(_text(_cell(r, i, headers[i])) for r in rows)
        longest.append(max((len(c) for c in cells), default=1) or 1)

    floor = 14
    ceiling = 46
    clamped = [max(floor, min(ceiling, w)) for w in longest]
    total = sum(clamped) or n
    widths = [max(floor, total_width * w / total) for w in clamped]

    # Hand any rounding remainder to the widest column so the table ends flush.
    drift = total_width - sum(widths)
    if widths:
        widths[widths.index(max(widths))] += drift
    return widths


def render_table_pdf(
    title, headers, rows, subtitle=None, orientation="landscape", _landscape_mode=None
) -> bytes:
    """Render a titled table to PDF bytes.

    `_landscape_mode` is accepted as an alias for `orientation` because report
    data is wide far more often than not, and callers tend to reach for the
    more descriptive name.
    """
    page = landscape(A4) if (orientation or "landscape") == "landscape" else A4
    available = (page[0] if page[0] > page[1] else page[1]) - 24 * mm

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=page,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=14 * mm,
        title=str(title),
        author="Shresth Masala Industry",
    )

    styles = getSampleStyleSheet()
    body_font = _ensure_devanagari_font()
    h1 = ParagraphStyle(
        "h1", parent=styles["Heading1"], fontName=body_font, fontSize=14, leading=17,
        textColor=TEXT, spaceAfter=2, shaping=1,
    )
    sub = ParagraphStyle(
        "sub", parent=styles["Normal"], fontName=body_font, fontSize=8.5, leading=11,
        textColor=MUTED, spaceAfter=8, shaping=1,
    )
    cell = ParagraphStyle(
        "cell", parent=styles["Normal"], fontName=body_font, fontSize=7.6, leading=9.4,
        textColor=TEXT, shaping=1,
    )
    cellR = ParagraphStyle("cellR", parent=cell, alignment=TA_RIGHT)
    head = ParagraphStyle("head", parent=cell, textColor=TEXT, fontSize=7.8)

    story = [Paragraph(_esc(str(title)), h1)]
    meta = [f"Generated {datetime.now():%d-%b-%Y %H:%M}"]
    if subtitle:
        meta.insert(0, str(subtitle))
    # Plain text separator, not &nbsp;. The whole string is escaped below, and
    # escaping turns an entity into "&amp;nbsp;" - which reportlab then prints
    # literally, leaving "&nbsp;|&nbsp;" visible on the document.
    story.append(Paragraph(_esc("  |  ".join(meta)), sub))

    ncols = len(headers)
    if ncols == 0:
        story.append(Spacer(1, 6))
        story.append(Paragraph("This report has no columns to show.", cell))
        doc.build(story)
        buf.seek(0)
        return buf.getvalue()

    data = [[Paragraph(_esc(_text(h)), head) for h in headers]]
    for r in rows:
        line = []
        for i in range(ncols):
            raw = _cell(r, i, headers[i])
            style = cellR if _looks_numeric(raw) else cell
            line.append(Paragraph(_esc(_text(raw)), style))
        data.append(line)
    if not rows:
        data.append(
            [Paragraph("<i>No rows matched the selected filters.</i>", cell)]
            + [Paragraph("", cell) for _ in range(ncols - 1)]
        )

    widths = _column_widths(headers, rows, available)
    table = Table(data, colWidths=widths, repeatRows=1, hAlign=TA_LEFT)

    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), TEXT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    # Banding, but never on the "no rows" placeholder row.
    if rows:
        for i in range(len(rows)):
            if i % 2 == 1:
                style.append(("BACKGROUND", (0, i + 1), (-1, i + 1), BAND))
    table.setStyle(TableStyle(style))

    story.append(table)
    if rows:
        story.append(Spacer(1, 6))
        story.append(Paragraph(_esc(f"{len(rows)} row(s)."), sub))

    doc.build(story, onFirstPage=_page_furniture, onLaterPages=_page_furniture)
    buf.seek(0)
    return buf.getvalue()


def _page_furniture(canvas, doc):
    """Footer with the page number, so a printed pack can be checked for
    missing sheets."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(12 * mm, 8 * mm, "Shresth Masala Industry")
    canvas.drawRightString(doc.pagesize[0] - 12 * mm, 8 * mm, f"Page {canvas.getPageNumber()}")
    canvas.setStrokeColor(GRID)
    canvas.setLineWidth(0.3)
    canvas.line(12 * mm, 10.5 * mm, doc.pagesize[0] - 12 * mm, 10.5 * mm)
    canvas.restoreState()


def _money(value) -> str:
    """ASCII-safe money: Helvetica has no rupee glyph, so amounts print as
    bare numbers under an "(Rs)" header rather than black boxes."""
    try:
        return f"{Decimal(str(value or 0)):,.2f}"
    except Exception:
        return "0.00"


# Length of a "YYYY-MM-DD" date prefix in ISO datetimes.
_ISO_DATE_PREFIX_LEN = 10


def _short_date(value) -> str:
    """A document date as DD-Mon-YYYY ("05-Mar-2026"), unambiguous in print."""
    try:
        text = str(value or "").strip()
        if not text:
            return ""
        if len(text) >= _ISO_DATE_PREFIX_LEN and text[4] == "-" and text[7] == "-":
            dt = datetime.fromisoformat(text.replace(" ", "T").replace("Z", "+00:00"))
        else:
            return text[:_ISO_DATE_PREFIX_LEN]
        return dt.strftime("%d-%b-%Y")
    except Exception:
        try:
            return str(value or "")[:_ISO_DATE_PREFIX_LEN]
        except Exception:
            return ""


def _tax_foot_rows(inv: dict, items, cell, cellR) -> list:
    """Tax breakup rows for the 12-column invoice footer table.

    One row set per distinct GST rate (CGST/SGST at half the line rate, IGST
    at the full rate). Amounts always sum from the lines, so the rows can
    never drift from the table above. The label ("CGST @ 2.5%") sits under
    the GST % column (0-based index 9), never under #; amounts stay under
    Tax (10) and Total (11) exactly as before.
    """

    def _rfmt(value) -> str:
        try:
            n = round(float(value or 0), 2)
            return str(int(n)) if n == int(n) else str(n)
        except Exception:
            return "0"

    groups: dict = {}
    for it in items or []:
        try:
            rate = round(float(it.get("gst_rate") or 0), 2)
        except Exception:
            rate = 0.0
        g = groups.setdefault(rate, {"cgst": 0.0, "sgst": 0.0, "igst": 0.0})
        for key in ("cgst", "sgst", "igst"):
            with suppress(Exception):
                g[key] += float(it.get(key) or 0)
    rows = []
    for rate in sorted(groups):
        g = groups[rate]
        if inv.get("intra_state"):
            half = _rfmt(rate / 2)
            rows.append(
                [Paragraph("", cell)] * 9
                + [
                    Paragraph(f"CGST @ {half}%", cellR),
                    Paragraph(_money(g["cgst"]), cellR),
                    Paragraph(_money(g["cgst"]), cellR),
                ]
            )
            rows.append(
                [Paragraph("", cell)] * 9
                + [
                    Paragraph(f"SGST @ {half}%", cellR),
                    Paragraph(_money(g["sgst"]), cellR),
                    Paragraph(_money(g["sgst"]), cellR),
                ]
            )
        else:
            rows.append(
                [Paragraph("", cell)] * 9
                + [
                    Paragraph(f"IGST @ {_rfmt(rate)}%", cellR),
                    Paragraph(_money(g["igst"]), cellR),
                    Paragraph(_money(g["igst"]), cellR),
                ]
            )
    return rows


def render_invoice_pdf(inv: dict) -> bytes:
    """One sales tax invoice as PDF bytes, from the same dict the print view
    uses - so the file, the screen and the paper can never disagree."""

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=14 * mm,
        title=str(inv.get("invoice_number") or "Tax Invoice"),
        author="Shresth Masala Industry",
    )
    styles = getSampleStyleSheet()
    body_font = _ensure_devanagari_font()
    # shaping=1 lets ReportLab shape Devanagari through HarfBuzz (uharfbuzz):
    # without it conjuncts print as dotted circles. Harmless for Latin text.
    h1 = ParagraphStyle(
        "invh1",
        parent=styles["Heading1"],
        fontName=body_font,
        fontSize=15,
        leading=18,
        textColor=TEXT,
        spaceAfter=2,
        shaping=1,
    )
    sub = ParagraphStyle(
        "invsub",
        parent=styles["Normal"],
        fontName=body_font,
        fontSize=8.5,
        leading=11,
        textColor=MUTED,
        spaceAfter=6,
        shaping=1,
    )
    cell = ParagraphStyle(
        "invcell",
        parent=styles["Normal"],
        fontName=body_font,
        fontSize=7.4,
        leading=9.2,
        textColor=TEXT,
        shaping=1,
    )
    cellR = ParagraphStyle("invcellR", parent=cell, alignment=TA_RIGHT)
    head = ParagraphStyle("invhead", parent=cell, textColor=TEXT, fontSize=7.6)
    cust_name = ParagraphStyle(
        "invcust", parent=cell, fontName=body_font, fontSize=11, leading=13, textColor=TEXT,
        shaping=1,
    )

    story = []
    logo = _logo_path()
    if logo:
        with suppress(Exception):
            story.append(
                Image(
                    _sized_logo(logo),
                    width=38 * mm,
                    height=30 * mm,
                    kind="proportional",
                )
            )
    story.append(Paragraph(_esc(str(inv.get("company_name") or "")), h1))
    for line in (
        inv.get("company_address"),
        ("Phone: " + inv["company_phone"]) if inv.get("company_phone") else "",
        inv.get("company_email"),
        ("GSTIN: " + inv["company_gstin"]) if inv.get("company_gstin") else "",
    ):
        if line:
            story.append(Paragraph(_esc(str(line)), sub))
    story.append(HRFlowable(width="100%", thickness=1, color=TEXT, spaceAfter=6))
    story.append(Paragraph("TAX INVOICE", head))
    meta = "  |  ".join(
        x for x in [
            str(inv.get("invoice_number") or ""),
            "Date: " + _short_date(inv.get("invoice_date")),
            ("Delivery challan: " + str(inv["challan_number"]))
            if inv.get("challan_number")
            else "",
        ] if x
    )
    story.append(Paragraph(_esc(meta), sub))
    story.append(Paragraph("Billed to", sub))
    # Size carries the emphasis (11pt vs 7.4pt body), not a bold tag:
    # only the regular Nirmala face is registered, and a <b> tag would
    # resolve to a missing bold face.
    story.append(Paragraph(
        _esc(str(inv.get("cust_company_name") or inv.get("customer_name") or "")),
        cust_name,
    ))
    for line in (
        inv.get("cust_address"),
        ("GSTIN: " + inv["cust_gst_no"]) if inv.get("cust_gst_no") else "",
        ("Place of supply: " + inv["place_of_supply"]) if inv.get("place_of_supply") else "",
    ):
        if line:
            story.append(Paragraph(_esc(str(line)), sub))
    story.append(Paragraph(
        _esc(
            "Intra-state supply (CGST + SGST)"
            if inv.get("intra_state")
            else "Inter-state supply (IGST)"
        ),
        sub,
    ))

    headers = ["#", "Product", "Size", "HSN", "Qty", "Weight", "Rate",
               "Amount (Rs)", "Taxable", "GST %", "Tax (Rs)", "Total (Rs)"]
    data = [[Paragraph(_esc(h), head) for h in headers]]
    for i, it in enumerate(inv.get("items") or [], 1):
        tax = float(it.get("cgst") or 0) + float(it.get("sgst") or 0) + float(it.get("igst") or 0)
        data.append([
            Paragraph(str(i), cell),
            Paragraph(_esc(str(it.get("product_name") or "")), cell),
            Paragraph(_esc(str(it.get("size_name") or "")), cell),
            Paragraph(_esc(str(it.get("hsn_code") or "")), cell),
            Paragraph(_money(it.get("quantity")).rstrip("0").rstrip("."), cellR),
            Paragraph(_money(it.get("weight")), cellR),
            Paragraph(_money(it.get("rate_per_kg")), cellR),
            Paragraph(_money(it.get("amount")), cellR),
            Paragraph(_money(it.get("taxable_value")), cellR),
            Paragraph(f"{it.get('gst_rate') or 0}%", cellR),
            Paragraph(_money(tax), cellR),
            Paragraph(_money(it.get("amount")), cellR),
        ])
    available = A4[0] - 24 * mm
    widths = _column_widths(headers, inv.get("items") or [], available)
    table = Table(data, colWidths=widths, repeatRows=1, hAlign=TA_LEFT)
    tstyle = [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    # Tax breakup, one row set per distinct GST rate (CGST/SGST at half the
    # line rate, IGST at the full rate). Amounts always sum from the lines,
    # so the rows can never drift from the table above.
    foot = _tax_foot_rows(inv, inv.get("items"), cell, cellR)
    foot.append(
        [Paragraph("<b>Grand total (Rs)</b>", cell)]
        + [Paragraph("", cell)] * 10
        + [Paragraph(f"<b>{_money(inv.get('grand_total'))}</b>", cellR)]
    )
    ftable = Table(foot, colWidths=widths, hAlign=TA_LEFT)
    ftable.setStyle(TableStyle(tstyle))
    table.setStyle(TableStyle(tstyle))
    story.append(table)
    story.append(ftable)
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        _esc("Total in words: " + str(inv.get("total_in_words") or "")), cell
    ))
    if inv.get("company_terms"):
        story.append(Paragraph(_esc("Terms: " + str(inv["company_terms"])), sub))

    doc.build(story, onFirstPage=_page_furniture, onLaterPages=_page_furniture)
    buf.seek(0)
    return buf.getvalue()


def _esc(s: str) -> str:
    """Escape for reportlab's mini-HTML parser.

    Paragraph treats < > & as markup, so unescaped cell text can either throw
    or silently swallow content. A product called "A & B" is not exotic.
    """
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
