"""HTML to markdown cleaner for job descriptions.

Wraps markdownify with sane defaults (ATX headings, drop script/style),
plus a whitespace pass to collapse runs of >2 blank lines.

Intended consumer: app.services.job_service.upsert_job, which writes
description_clean alongside the raw description_md.
"""

import re

from bs4 import BeautifulSoup
from markdownify import markdownify


def clean_html_to_markdown(html: str | None) -> str:
    """Convert raw HTML to compact markdown. Returns '' for None/empty input."""
    if not html:
        return ""
    # Pre-strip script/style with their text content. markdownify's `strip=`
    # removes the tags but keeps inner text, which would leak JS/CSS into output.
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    md = markdownify(str(soup), heading_style="ATX")
    return re.sub(r"\n{3,}", "\n\n", md).strip()
