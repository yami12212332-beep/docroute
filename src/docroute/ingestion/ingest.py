"""
One call per input file: route pages (native vs forced OCR), merge HTML,
dump tables, extract + store images. This is the module cli.py calls.
"""

from pathlib import Path

from docling.document_converter import DocumentConverter

from docroute.config import OUTPUT_DIR
from docroute.images.extractor import extract_images
from docroute.images.storage import save_image
from docroute.pipeline.router import merge_pages_html, route_document


def check_for_glyph_artifacts(text: str, label: str) -> None:
    """#2737: force_full_page_ocr does not always propagate into table
    cells -- TableStructureModel can still read stale word_cells from the
    PDF backend. Upstream Docling bug, not fixable from pipeline options,
    so just flag it for manual review."""
    if "GLYPH<" in text:
        print(f"  !! WARNING: GLYPH artifacts found in {label} -- known Docling issue #2737")


def dump_tables(docling_result, doc_id: str, page_number: int, output_dir: Path) -> None:
    for i, table in enumerate(docling_result.document.tables, start=1):
        df = table.export_to_dataframe(doc=docling_result.document)
        csv_path = output_dir / f"{doc_id}_p{page_number:03d}_table{i}.csv"
        df.to_csv(csv_path, index=False)
        check_for_glyph_artifacts(df.to_string(), csv_path.name)
        print(f"    table {i}: {df.shape[0]}x{df.shape[1]} -> {csv_path}")


def ingest_pdf(
    pdf_path: Path,
    output_dir: Path | None = None,
    native_converter: DocumentConverter | None = None,
    ocr_converter: DocumentConverter | None = None,
) -> dict:
    """
    Runs the full pipeline on one PDF:
      1. Route every page (native text vs forced OCR)
      2. Merge into one HTML document
      3. Dump tables per page
      4. Extract + dedup-store images per page

    output_dir defaults to config.OUTPUT_DIR (old CLI behavior). Pass an
    explicit per-job directory when calling this from something that may
    process multiple documents concurrently (e.g. an API), so jobs don't
    write into the same shared output folder.

    native_converter/ocr_converter are forwarded to route_document() --
    see its docstring. Pass pre-built ones from a long-lived caller to
    avoid reloading the OCR model per document.

    Returns a summary dict -- swap the print statements / return value for
    whatever your actual ingestion service needs (DB writes, queue events).
    """
    doc_id = pdf_path.stem
    output_dir = Path(output_dir) if output_dir is not None else Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    images_dir = output_dir / "images"

    print(f"Ingesting {pdf_path.name} ...")
    page_results = route_document(pdf_path, native_converter=native_converter, ocr_converter=ocr_converter)

    merged_html = merge_pages_html(page_results)
    check_for_glyph_artifacts(merged_html, pdf_path.name)
    html_path = output_dir / f"{doc_id}.html"
    html_path.write_text(merged_html, encoding="utf-8")
    print(f"-> {html_path}")

    image_records = []
    corrected_text_paths = []
    for pr in page_results:
        dump_tables(pr.docling_result, doc_id, pr.page_number, output_dir)
        for extracted in extract_images(pr.docling_result):
            record = save_image(
                extracted.image, doc_id, pr.page_number, extracted.ref, out_dir=images_dir
            )
            image_records.append(record)

        # label_value_form pages get a row-ordered PLAIN TEXT file alongside
        # the HTML -- the HTML above still reflects Docling's (wrong, for
        # these pages) column order, since the override only reconstructs
        # text, not full HTML structure. Downstream chunking/embedding for
        # these pages should read the corrected .txt, not the .html.
        if pr.corrected_text is not None:
            corrected_path = output_dir / f"{doc_id}_p{pr.page_number:03d}_corrected.txt"
            corrected_path.write_text(pr.corrected_text, encoding="utf-8")
            corrected_text_paths.append(str(corrected_path))
            print(f"    corrected reading order -> {corrected_path}")

    if image_records:
        print(f"  extracted {len(image_records)} image(s) -> {output_dir / 'images'}")

    ocr_pages = sum(1 for pr in page_results if pr.used_ocr)
    print(f"  {ocr_pages}/{len(page_results)} page(s) required forced OCR")

    escalation_pages = [pr.page_number for pr in page_results if pr.needs_escalation]
    if escalation_pages:
        print(
            f"  !! {len(escalation_pages)} page(s) had ambiguous layout and "
            f"were NOT reading-order-corrected (pages {escalation_pages}) -- "
            f"needs Tier 2 vision-model check, not yet implemented"
        )

    return {
        "doc_id": doc_id,
        "html_path": str(html_path),
        "pages": len(page_results),
        "ocr_pages": ocr_pages,
        "images": image_records,
        "corrected_text_paths": corrected_text_paths,
        "escalation_pages": escalation_pages,
    }
