"""Unit tests for app.services.html_cleaner."""

from app.services.html_cleaner import clean_html_to_markdown


def test_returns_empty_string_for_none_input():
    assert clean_html_to_markdown(None) == ""


def test_returns_empty_string_for_empty_input():
    assert clean_html_to_markdown("") == ""


def test_strips_html_tags_and_returns_markdown():
    html = "<h2>Requirements</h2><ul><li><strong>Python</strong> 5+ years</li></ul>"
    out = clean_html_to_markdown(html)
    assert "## Requirements" in out
    assert "**Python**" in out
    assert "5+ years" in out
    assert "<h2>" not in out
    assert "<li>" not in out


def test_drops_script_and_style_tags():
    html = "<p>visible</p><script>alert(1)</script><style>.x{}</style>"
    out = clean_html_to_markdown(html)
    assert "visible" in out
    assert "alert" not in out
    assert ".x{}" not in out


def test_collapses_excessive_blank_lines():
    html = "<p>a</p>\n\n\n\n<p>b</p>"
    out = clean_html_to_markdown(html)
    assert "\n\n\n" not in out
    assert "a" in out and "b" in out


def test_entity_encoded_html_is_decoded_then_converted_to_markdown():
    # Greenhouse's boards-api delivers `content` as HTML-entity-encoded HTML —
    # the string literally contains "&lt;h2&gt;" not "<h2>". Without an
    # html.unescape() before BeautifulSoup, the cleaner just decodes entities
    # and never sees real tags, so markdownify is a no-op and we end up
    # storing decoded HTML in jobs.description. Verified against a real
    # Stripe posting on 2026-05-10.
    encoded = (
        "&lt;h2&gt;&lt;strong&gt;Who we are&lt;/strong&gt;&lt;/h2&gt;\n"
        "&lt;p&gt;Stripe is a financial infrastructure platform.&lt;/p&gt;\n"
        "&lt;ul&gt;&lt;li&gt;Item &amp;nbsp;one&lt;/li&gt;&lt;/ul&gt;"
    )
    out = clean_html_to_markdown(encoded)
    assert "## " in out  # H2 became markdown heading
    assert "**Who we are**" in out
    assert "Stripe is a financial infrastructure platform." in out
    assert "* Item" in out  # list became markdown
    # No leftover tags as text — neither decoded nor entity-encoded.
    assert "<h2>" not in out
    assert "<p>" not in out
    assert "<li>" not in out
    assert "&lt;" not in out


def test_already_markdown_input_is_idempotent_enough():
    md = "## Hello\n\n* item one\n* item two\n"
    out = clean_html_to_markdown(md)
    # Markdownify on already-markdown is a near no-op; must not lose content.
    assert "Hello" in out
    assert "item one" in out
    assert "item two" in out
