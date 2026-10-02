"""
Splits a PDF into single-page files so each page can be routed and
converted independently (native text vs forced OCR).

This also happens to be the fix for the earlier "force_full_page_ocr
degrades on long documents" bug (Docling #1499/#1744) -- since we're now
always converting page-by-page for routing purposes, that bug class is
sidestepped as a side effect, not something we need to special-case.
"""

from pathlib import Path

import pymupdf


def get_page_text(pdf_path: Path, page_index: int) -> str:
    """Native-text extraction for a single page, used by the quality
    scorer. Cheap -- no OCR involved."""
    doc = pymupdf.open(pdf_path)
    try:
        return doc[page_index].get_text()
    finally:
        doc.close()


def split_pdf_pages(pdf_path: Path, tmp_dir: Path) -> list[Path]:
    """Split a PDF into single-page PDF files, written into tmp_dir.
    Returns paths in page order."""
    src = pymupdf.open(pdf_path)
    page_paths = []
    try:
        for i in range(src.page_count):
            single = pymupdf.open()
            single.insert_pdf(src, from_page=i, to_page=i)
            out_path = tmp_dir / f"{pdf_path.stem}_p{i + 1:03d}.pdf"
            single.save(out_path)
            single.close()
            page_paths.append(out_path)
    finally:
        src.close()
    return page_paths


def page_count(pdf_path: Path) -> int:
    doc = pymupdf.open(pdf_path)
    try:
        return doc.page_count
    finally:
        doc.close()
