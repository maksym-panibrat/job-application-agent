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


def test_already_markdown_input_is_idempotent_enough():
    md = "## Hello\n\n* item one\n* item two\n"
    out = clean_html_to_markdown(md)
    # Markdownify on already-markdown is a near no-op; must not lose content.
    assert "Hello" in out
    assert "item one" in out
    assert "item two" in out
