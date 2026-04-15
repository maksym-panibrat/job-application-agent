"""Document service — Markdown → PDF export via WeasyPrint.

WeasyPrint requires Cairo/Pango system libraries. Import is deferred to runtime
(inside export_pdf) so the app starts cleanly on macOS dev without those libs.
"""

import asyncio
import uuid

import markdown2
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.application import GeneratedDocument

log = structlog.get_logger()

PDF_CSS = """
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 11pt;
    line-height: 1.5;
    max-width: 800px;
    margin: 0 auto;
    padding: 20px;
    color: #1a1a1a;
}
h1 { font-size: 20pt; margin-bottom: 4px; }
h2 { font-size: 14pt; border-bottom: 1px solid #ccc; padding-bottom: 4px; margin-top: 16px; }
h3 { font-size: 12pt; margin-bottom: 4px; }
ul { margin: 4px 0; padding-left: 20px; }
li { margin: 2px 0; }
strong { font-weight: 600; }
"""


async def export_pdf(doc_id: uuid.UUID, session: AsyncSession) -> bytes:
    """
    Render a GeneratedDocument to PDF bytes.
    WeasyPrint is synchronous + CPU-bound, so we use run_in_executor (F7).
    """
    doc = await session.get(GeneratedDocument, doc_id)
    if not doc:
        raise ValueError(f"Document {doc_id} not found")

    effective_md = doc.user_edited_md or doc.content_md
    html_body = markdown2.markdown(
        effective_md, extras=["tables", "fenced-code-blocks", "strike"]
    )
    full_html = f"<html><head><style>{PDF_CSS}</style></head><body>{html_body}</body></html>"

    import weasyprint  # lazy import — requires Cairo/Pango (available in Docker, not macOS dev)

    loop = asyncio.get_event_loop()
    pdf_bytes = await loop.run_in_executor(
        None, lambda: weasyprint.HTML(string=full_html).write_pdf()
    )
    return pdf_bytes
