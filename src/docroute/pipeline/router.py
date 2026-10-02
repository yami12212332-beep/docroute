"""
The core of the pipeline: for a given PDF, decide per page whether to
trust native text or force OCR, convert each page with the right
converter, and merge the results back into one document.

This replaces the old "split only if >10 pages" / "one mode for the whole
document" logic entirely -- every PDF is now split and routed per page,
regardless of length, since the quality scorer is cheap (no OCR needed
just to decide).

Also runs Tier 1 layout classification on every page after conversion
(pipeline/layout_classifier.py) to catch a separate problem from OCR-vs-
native: whether Docling's READING ORDER can be trusted at all. A
label:value form (narrow labels paired row-by-row with wider text, e.g.
"TREATMENT GIVEN : ...") gets misread by Docling's column-based ordering
as "all labels, then all values" -- Tier 1 detects that pattern
geometrically and swaps in the row-based override from
pipeline/reading_order.py. Pages the classifier can't confidently place
(layout_type == "ambiguous") are flagged needs_escalation=True for a
Tier 2 vision-model check -- NOT YET IMPLEMENTED. Those pages currently
fall back to Docling's default ordering with a printed warning, which
means their reading order is unverified, not corrected.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path

from docling.document_converter import DocumentConverter

from docroute.pipeline.converter import build_native_converter, build_ocr_converter
from docroute.pipeline.layout_classifier import classify_layout, extract_text_blocks
from docroute.pipeline.quality_scorer import page_needs_ocr
from docroute.pipeline.reading_order import build_row_ordered_text
from docroute.pipeline.splitter import get_page_text, page_count, split_pdf_pages


@dataclass
class PageResult:
    page_number: int
    used_ocr: bool
    html: str
    docling_result: object  # docling ConversionResult, for table/image extraction downstream
    layout_type: str = "single_column"
    layout_confidence: float = 1.0
    needs_escalation: bool = False
    corrected_text: str | None = None  # only set for label_value_form pages


def route_document(
    pdf_path: Path,
    native_converter: DocumentConverter | None = None,
    ocr_converter: DocumentConverter | None = None,
) -> list[PageResult]:
    """
    Splits pdf_path into pages, scores each one, converts each with the
    matching converter (native or forced-OCR), and returns per-page
    results in page order.

    native_converter/ocr_converter default to a fresh build each call
    (old behavior, fine for the CLI processing one file at a time). Pass
    pre-built converters -- built once at process startup -- when calling
    this repeatedly in a long-lived process (e.g. an API worker), since
    building a converter loads the OCR model and is expensive to redo per
    document.
    """
    native_converter = native_converter or build_native_converter()
    ocr_converter = ocr_converter or build_ocr_converter()

    total_pages = page_count(pdf_path)
    results: list[PageResult] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        page_paths = split_pdf_pages(pdf_path, tmp_dir)

        for i, page_path in enumerate(page_paths):
            page_number = i + 1
            native_text = get_page_text(pdf_path, i)
            needs_ocr = page_needs_ocr(native_text)

            converter = ocr_converter if needs_ocr else native_converter
            result = converter.convert(str(page_path))
            html = result.document.export_to_html()

            layout = classify_layout(result)
            corrected_text = None
            layout_note = layout.layout_type
            if layout.layout_type == "label_value_form" and not layout.needs_escalation:
                blocks = extract_text_blocks(result)
                corrected_text = build_row_ordered_text(blocks)
                layout_note += " -> row-order override applied"
            elif layout.needs_escalation:
                layout_note += " -> AMBIGUOUS, needs Tier 2 (not implemented), using Docling's default order"

            print(
                f"  page {page_number}/{total_pages}: "
                f"{'forced OCR' if needs_ocr else 'native text'}, "
                f"layout={layout_note} (confidence={layout.confidence:.2f})"
            )

            results.append(
                PageResult(
                    page_number=page_number,
                    used_ocr=needs_ocr,
                    html=html,
                    docling_result=result,
                    layout_type=layout.layout_type,
                    layout_confidence=layout.confidence,
                    needs_escalation=layout.needs_escalation,
                    corrected_text=corrected_text,
                )
            )

    return results


def merge_pages_html(page_results: list[PageResult]) -> str:
    """Simple merge -- page break between each page's HTML. Swap for a
    real DoclingDocument merge if you need a single coherent document
    object downstream rather than just HTML output."""
    return "\n<hr>\n".join(pr.html for pr in page_results)
