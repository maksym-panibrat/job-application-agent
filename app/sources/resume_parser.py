import io
from typing import Literal


def detect_format(filename: str) -> Literal["pdf", "docx", "txt"]:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith(".docx"):
        return "docx"
    return "txt"


def parse_pdf(raw_bytes: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw_bytes))
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(text.strip())
    return "\n\n".join(p for p in pages if p)


def parse_docx(raw_bytes: bytes) -> str:
    import docx

    doc = docx.Document(io.BytesIO(raw_bytes))
    lines = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = para.style.name if para.style else ""
        if "Heading 1" in style:
            lines.append(f"# {text}")
        elif "Heading 2" in style:
            lines.append(f"## {text}")
        elif "Heading 3" in style:
            lines.append(f"### {text}")
        else:
            lines.append(text)
    return "\n\n".join(lines)


def parse_resume(filename: str, raw_bytes: bytes) -> str:
    fmt = detect_format(filename)
    if fmt == "pdf":
        return parse_pdf(raw_bytes)
    if fmt == "docx":
        return parse_docx(raw_bytes)
    return raw_bytes.decode("utf-8", errors="replace")
