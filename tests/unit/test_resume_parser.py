import os


def setup_env():
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


def test_detect_format_pdf():
    setup_env()
    from app.sources.resume_parser import detect_format

    assert detect_format("resume.pdf") == "pdf"
    assert detect_format("MY_RESUME.PDF") == "pdf"


def test_detect_format_docx():
    setup_env()
    from app.sources.resume_parser import detect_format

    assert detect_format("cv.docx") == "docx"
    assert detect_format("Resume.DOCX") == "docx"


def test_detect_format_txt_fallback():
    setup_env()
    from app.sources.resume_parser import detect_format

    assert detect_format("resume.txt") == "txt"
    assert detect_format("resume.md") == "txt"
    assert detect_format("unknown") == "txt"


def test_parse_resume_txt():
    setup_env()
    from app.sources.resume_parser import parse_resume

    content = "John Doe\nSoftware Engineer"
    result = parse_resume("resume.txt", content.encode("utf-8"))
    assert "John Doe" in result
    assert "Software Engineer" in result
