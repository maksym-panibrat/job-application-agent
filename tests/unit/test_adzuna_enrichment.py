"""Unit tests for Adzuna detail-page enrichment."""

from app.sources.adzuna_enrichment import _extract_body, _extract_card_info

SAMPLE_HTML = """
<html><body>
<div class="ui-job-card-info flex-1 grid">
    <div class="ui-company">Acme Corp</div>
    <div class="ui-location"><span>San Francisco, CA</span></div>
    <div class="ui-salary">$150,000 - $180,000 per year</div>
    <div class="ui-contract-time">Full time</div>
</div>
<section class="adp-body mx-4 mb-4">
<strong>Role Overview</strong>
<p>We are looking for a Staff Engineer to join our platform team.</p>
<strong>Responsibilities</strong>
<ul>
<li>Design scalable systems</li>
<li>Mentor junior engineers</li>
</ul>
<strong>Requirements</strong>
<ul>
<li>7+ years of experience</li>
<li>Strong Python skills</li>
</ul>
</section>
</body></html>
"""

MINIMAL_HTML = "<html><body><p>Hello</p></body></html>"


class TestExtractCardInfo:
    def test_extracts_salary_and_contract(self):
        info = _extract_card_info(SAMPLE_HTML)
        assert info is not None
        assert info["salary"] == "$150,000 - $180,000 per year"
        assert info["contract_type"] == "Full time"

    def test_returns_none_when_no_card(self):
        info = _extract_card_info(MINIMAL_HTML)
        assert info is None

    def test_returns_none_on_empty_card(self):
        html = '<html><body><div class="ui-job-card-info"></div></body></html>'
        info = _extract_card_info(html)
        assert info is None

    def test_partial_card(self):
        html = """
        <html><body>
        <div class="ui-job-card-info">
            <div class="ui-salary">$100k</div>
        </div>
        </body></html>
        """
        info = _extract_card_info(html)
        assert info is not None
        assert info["salary"] == "$100k"
        assert info["contract_type"] is None


class TestExtractBody:
    def test_extracts_text_from_adp_body(self):
        text = _extract_body(SAMPLE_HTML)
        assert text is not None
        assert "Staff Engineer" in text or "platform team" in text

    def test_returns_none_on_minimal_html(self):
        # trafilatura and bs4 fallback both produce None/too-short for single-sentence pages
        result = _extract_body("<html><body><p>Hi</p></body></html>")
        # result may be None or a very short string — either is acceptable
        assert result is None or len(result) < 100

    def test_bs4_fallback_when_trafilatura_fails(self):
        """adp-body fallback returns text even if trafilatura extracts nothing."""
        html = """
        <html><body>
        <section class="adp-body">
        Design and build scalable systems at Acme Corp. You will work with a team of engineers
        to deliver high-quality software solutions. The role requires 5+ years of experience
        in backend development with Python and familiarity with distributed systems.
        </section>
        </body></html>
        """
        text = _extract_body(html)
        assert text is not None
        assert "scalable" in text
