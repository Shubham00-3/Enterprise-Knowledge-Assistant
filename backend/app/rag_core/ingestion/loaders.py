from pathlib import Path

import pdfplumber
from docx import Document as DocxDocument

from app.rag_core.interfaces import LoadedPage


SUPPORTED_EXTENSIONS = {".pdf", ".md", ".txt", ".docx"}


def load_document(path: Path) -> list[LoadedPage]:
    suffix = path.suffix.lower()
    title = path.stem.replace("_", " ").replace("-", " ").strip()
    if suffix == ".pdf":
        return _load_pdf(path, title)
    if suffix in {".md", ".txt"}:
        return _load_text(path, title)
    if suffix == ".docx":
        return _load_docx(path, title)
    raise ValueError(f"Unsupported document type: {path.suffix}")


def _load_pdf(path: Path, title: str) -> list[LoadedPage]:
    pages: list[LoadedPage] = []
    with pdfplumber.open(path) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            pages.append(LoadedPage(text=page.extract_text() or "", page=index, title=title))
    return pages


def _load_text(path: Path, title: str) -> list[LoadedPage]:
    text = path.read_text(encoding="utf-8")
    parts = text.split("\n--- page ---\n")
    return [LoadedPage(text=part.strip(), page=i, title=title) for i, part in enumerate(parts, start=1)]


def _load_docx(path: Path, title: str) -> list[LoadedPage]:
    doc = DocxDocument(path)
    text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    return [LoadedPage(text=text, page=1, title=title)]
