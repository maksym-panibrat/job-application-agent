"""
Adzuna detail-page enrichment.

Fetches the full job description and card metadata from an Adzuna redirect_url.
The Adzuna search API returns truncated descriptions; the detail page has the full text.
"""

import httpx
import structlog
import trafilatura
from bs4 import BeautifulSoup

log = structlog.get_logger()


async def fetch_full_description(
    redirect_url: str,
) -> tuple[str | None, dict | None, str | None]:
    """
    Fetch full job description and card metadata from an Adzuna redirect URL.

    Follows redirects and captures the final destination URL.
    When the final URL is not on adzuna.com, Adzuna-specific CSS selectors are skipped
    and only trafilatura is used for description extraction.

    Returns:
        (description_text, card_info, resolved_url) where:
        - description_text: extracted main text or None
        - card_info: dict with salary/contract_type keys (Adzuna pages only) or None
        - resolved_url: the final URL after following redirects, or None on failure
    Returns (None, None, None) on any fetch failure.
    """
    await log.ainfo("adzuna.enrichment.attempt", url=redirect_url)
    try:
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; job-application-agent/1.0)"},
        ) as client:
            response = await client.get(redirect_url)
            response.raise_for_status()
            html = response.text
            resolved_url = str(response.url)
    except Exception as exc:
        await log.aerror(
            "adzuna.enrichment.failed",
            source_name="adzuna_enrichment",
            url=redirect_url,
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return None, None, None

    is_adzuna_page = "adzuna.com" in resolved_url.lower()
    description = _extract_body(html, use_adzuna_selector=is_adzuna_page)
    card_info = _extract_card_info(html) if is_adzuna_page else None
    salary_found = bool(card_info and card_info.get("salary"))
    await log.ainfo("adzuna.enrichment.success", url=redirect_url, salary=salary_found)
    return description, card_info, resolved_url


def _extract_body(html: str, *, use_adzuna_selector: bool = True) -> str | None:
    """Extract main job description text. Trafilatura preserves list/header structure."""
    text = trafilatura.extract(
        html,
        output_format="txt",
        include_tables=False,
        include_links=False,
        no_fallback=False,
    )
    if text and len(text.strip()) > 100:
        return text.strip()

    if use_adzuna_selector:
        # Fallback: bs4 on Adzuna-specific selector
        try:
            soup = BeautifulSoup(html, "html.parser")
            section = soup.select_one("section.adp-body")
            if section:
                return section.get_text(separator="\n", strip=True)
        except Exception as exc:
            log.error(
                "adzuna.enrichment.bs4_body_failed",
                source_name="adzuna_enrichment",
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )

    return None


def _extract_card_info(html: str) -> dict | None:
    """Extract structured metadata from the .ui-job-card-info div."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        card = soup.select_one(".ui-job-card-info")
        if not card:
            return None

        salary_el = card.select_one(".ui-salary")
        contract_el = card.select_one(".ui-contract-time")

        salary = salary_el.get_text(strip=True) if salary_el else None
        contract_type = contract_el.get_text(strip=True) if contract_el else None

        if not salary and not contract_type:
            return None

        return {"salary": salary or None, "contract_type": contract_type or None}
    except Exception as exc:
        log.error(
            "adzuna.enrichment.card_info_failed",
            source_name="adzuna_enrichment",
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return None
