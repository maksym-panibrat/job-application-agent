"""HTML to markdown cleaner for job descriptions.

Wraps markdownify with sane defaults (ATX headings, drop script/style),
plus a whitespace pass to collapse runs of >2 blank lines.

Intended consumer: app.services.job_service.upsert_job, which writes
description (markdown) alongside the raw description_raw.
"""

import html as html_lib
import re

from bs4 import BeautifulSoup
from markdownify import markdownify


def clean_html_to_markdown(raw: str | None) -> str:
    """Convert raw HTML to compact markdown. Returns '' for None/empty input.

    Decodes HTML entities BEFORE parsing. Greenhouse's boards-api delivers
    `content` as entity-encoded HTML (`&lt;h2&gt;...&lt;/h2&gt;`) — without
    the unescape, BeautifulSoup sees those entities as text, markdownify
    has no tags to convert, and decoded HTML ends up stored in
    jobs.description. Symptom: literal <p>/<h2>/<strong> rendered as
    visible characters on the match-detail page (logged 2026-05-10).
    """
    if not raw:
        return ""
    decoded = html_lib.unescape(raw)
    # Pre-strip script/style with their text content. markdownify's `strip=`
    # removes the tags but keeps inner text, which would leak JS/CSS into output.
    soup = BeautifulSoup(decoded, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    md = markdownify(str(soup), heading_style="ATX")
    return re.sub(r"\n{3,}", "\n\n", md).strip()
