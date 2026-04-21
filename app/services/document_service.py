"""Document service -- Markdown to PDF export via fpdf2 (pure Python, no system deps)."""

import asyncio
import uuid

import markdown2
import structlog
from bs4 import BeautifulSoup, NavigableString, Tag
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.application import GeneratedDocument

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------
_NAVY = (26, 54, 93)  # headings
_DARK = (40, 40, 40)  # body text
_MID = (100, 100, 100)  # secondary / metadata
_RULE = (200, 210, 220)  # horizontal rules under h2

# ---------------------------------------------------------------------------
# Character normalization (fpdf2 core fonts are latin-1 only)
# ---------------------------------------------------------------------------
_UNICODE_TO_ASCII = str.maketrans(
    {
        "\u2014": "-",  # em dash
        "\u2013": "-",  # en dash
        "\u2012": "-",  # figure dash
        "\u2010": "-",  # hyphen
        "\u2011": "-",  # non-breaking hyphen
        "\u201c": '"',  # left double quotation mark
        "\u201d": '"',  # right double quotation mark
        "\u2018": "'",  # left single quotation mark
        "\u2019": "'",  # right single quotation mark
        "\u2026": "...",  # ellipsis
        "\u00a0": " ",  # non-breaking space
        "\u2022": "-",  # bullet
        "\u00b7": "-",  # middle dot
        "\u00ae": "(R)",  # registered sign
        "\u00a9": "(C)",  # copyright sign
        "\u2122": "(TM)",  # trade mark sign
    }
)


def _clean(text: str) -> str:
    return text.translate(_UNICODE_TO_ASCII).encode("latin-1", errors="replace").decode("latin-1")


# ---------------------------------------------------------------------------
# Inline text writer (handles <strong>, <em>, <a>, plain text)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Block element renderers
# ---------------------------------------------------------------------------
def _render_heading(pdf, elem, margin_left: float) -> None:
    level = int(elem.name[1])
    text = _clean(elem.get_text().strip())
    if not text:
        return

    if level == 1:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", size=20)
        pdf.set_text_color(*_NAVY)
        pdf.multi_cell(0, 9, text)
        pdf.ln(1)
    elif level == 2:
        pdf.ln(5)
        pdf.set_font("Helvetica", "B", size=13)
        pdf.set_text_color(*_NAVY)
        pdf.multi_cell(0, 7, text.upper())
        # thin rule below
        y = pdf.get_y() + 1
        pdf.set_draw_color(*_RULE)
        pdf.line(margin_left, y, pdf.w - margin_left, y)
        pdf.ln(3)
    elif level == 3:
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", size=11)
        pdf.set_text_color(*_DARK)
        pdf.multi_cell(0, 6, text)
        pdf.ln(1)
    else:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", size=10.5)
        pdf.set_text_color(*_DARK)
        pdf.multi_cell(0, 5.5, text)
        pdf.ln(1)

    pdf.set_text_color(*_DARK)


def _render_paragraph(pdf, elem, size: float = 10.5, line_height: float = 5.5) -> None:
    text = _clean(elem.get_text()).strip()
    if not text:
        return
    pdf.set_font("Helvetica", size=size)
    pdf.set_text_color(*_DARK)
    pdf.multi_cell(0, line_height, text)
    pdf.ln(1)


def _render_list(pdf, elem, depth: int = 0) -> None:
    size = 10.5
    line_height = 5.5
    indent = 6 + depth * 5
    marker = "-"

    for li in elem.find_all("li", recursive=False):
        # marker
        pdf.set_font("Helvetica", size=size)
        pdf.set_text_color(*_DARK)
        pdf.set_x(pdf.l_margin + indent)
        pdf.write(line_height, f"{marker}  ")

        # inline content of this <li>
        # Collect direct text (excluding nested lists) then render nested lists after
        text_parts = []
        nested = []
        for child in li.children:
            if isinstance(child, Tag) and child.name in ("ul", "ol"):
                nested.append(child)
            elif isinstance(child, NavigableString):
                text_parts.append(str(child))
            elif isinstance(child, Tag):
                text_parts.append(child.get_text())

        text = _clean("".join(text_parts)).strip()
        if text:
            pdf.write(line_height, text)
        pdf.ln(line_height)
        pdf.set_x(pdf.l_margin)

        for sub in nested:
            _render_list(pdf, sub, depth=depth + 1)

    pdf.ln(1)


def _render_pre(pdf, elem) -> None:
    text = _clean(elem.get_text())
    pdf.set_font("Courier", size=9)
    pdf.set_text_color(*_MID)
    pdf.set_fill_color(245, 247, 250)
    pdf.multi_cell(0, 4.5, text, fill=True)
    pdf.ln(2)
    pdf.set_text_color(*_DARK)


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------
def _render_pdf(html: str) -> bytes:
    """Synchronous PDF render -- runs in executor to avoid blocking the event loop."""
    from fpdf import FPDF

    margin = 25.0
    pdf = FPDF()
    pdf.set_margins(margin, margin, margin)
    pdf.set_auto_page_break(auto=True, margin=margin)
    pdf.add_page()
    pdf.set_text_color(*_DARK)

    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("body") or soup

    for elem in body.children:
        if isinstance(elem, NavigableString):
            text = _clean(str(elem)).strip()
            if text:
                pdf.set_font("Helvetica", size=10.5)
                pdf.multi_cell(0, 5.5, text)
            continue

        if not isinstance(elem, Tag):
            continue

        name = elem.name
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            _render_heading(pdf, elem, margin)
        elif name == "p":
            _render_paragraph(pdf, elem)
        elif name in ("ul", "ol"):
            _render_list(pdf, elem)
        elif name == "pre":
            _render_pre(pdf, elem)
        elif name == "hr":
            pdf.ln(3)
            pdf.set_draw_color(*_RULE)
            pdf.line(margin, pdf.get_y(), pdf.w - margin, pdf.get_y())
            pdf.ln(3)
        elif name == "br":
            pdf.ln(5)

    return bytes(pdf.output())


async def export_pdf(doc_id: uuid.UUID, session: AsyncSession) -> bytes:
    doc = await session.get(GeneratedDocument, doc_id)
    if not doc:
        raise ValueError(f"Document {doc_id} not found")

    effective_md = doc.user_edited_md or doc.content_md
    html_body = markdown2.markdown(effective_md, extras=["tables", "fenced-code-blocks", "strike"])
    html = f"<html><body>{html_body}</body></html>"

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _render_pdf, html)
