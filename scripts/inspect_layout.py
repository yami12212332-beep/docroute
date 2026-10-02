"""
Run this LOCALLY, on your own machine with Docling actually installed
(`uv sync` in the project root first) -- it will not run in a sandbox
that doesn't have docling/rapidocr installed.

Converts one page (a single-page PDF, or an image file) with the real
Docling pipeline, runs the actual Tier 1 layout classifier against real
paragraph-level Docling output (not raw per-line OCR like the earlier
sandbox demo -- Docling merges wrapped lines into one block per
paragraph before this code ever sees it, so you should see far less
noise here than that demo did), and draws the result so you can eyeball
it directly.

Usage:
    uv run python scripts/inspect_layout.py path/to/document.pdf [page_number]
    uv run python scripts/inspect_layout.py path/to/scan.png

page_number defaults to 1. For a PDF, the script ALWAYS splits it into
single pages first (via pipeline/splitter.split_pdf_pages -- the same
function router.py uses in production) and converts only the requested
page in isolation.

This matters: an earlier version of this script handed the entire
multi-page PDF to Docling in one call and ran classify_layout() across
every page's blocks combined together. On a real multi-page hospital
record, that mixed page 1's discharge-summary fields with page 2's
continuation-sheet content in a single clustering pass, producing 4
"real-sized" (non-noise) clusters and an incorrect "ambiguous" result --
not because the classifier failed, but because it was handed two
different pages' layouts as if they were one page. router.py has never
had this bug; it was only in this standalone test script.

Output:
    <input_stem>_layout_annotated.png -- each detected cluster drawn in
    a different color, in the same directory as the input file.
    Console output: the classification decision and the raw features
    (n_clusters, raw_n_clusters, pairing_score, width_ratio) that
    produced it.

CAVEATS -- things this script assumes but can't verify without your
actual Docling install:
  1. Bounding-box coordinate origin: layout_classifier.py's
     _normalize_bbox_to_top_left() assumes it can detect and correct a
     bottom-left origin via bbox.coord_origin / to_top_left_origin().
     If your Docling version's BoundingBox API differs, run
     `_debug_print_blocks()` (imported below) FIRST and confirm y0
     increases going DOWN the page before trusting anything else here.
  2. Pixel scaling for drawing: Docling's rendered page image and its
     bounding boxes are assumed to share the same origin, with bbox
     coordinates in PDF points that need multiplying by
     IMAGE_RESOLUTION_SCALE to land in the rendered image's pixel space.
     If the drawn boxes don't line up with the visible text when you
     open the output PNG, this scaling assumption is what's wrong --
     print a known box's coordinates next to the image dimensions to
     debug it.
  3. `page.image.pil_image` is this script's guess at how to pull a PIL
     image out of Docling's PageItem. If that attribute path doesn't
     exist on your installed version, the script will raise
     AttributeError there specifically -- that tells you which line to
     fix, not that the whole approach is wrong.
"""

import sys
import tempfile
from pathlib import Path

from PIL import ImageDraw

from docroute.config import IMAGE_RESOLUTION_SCALE, LAYOUT_GAP_RATIO
from docroute.pipeline.converter import build_native_converter, build_ocr_converter
from docroute.pipeline.layout_classifier import (
    classify_layout,
    cluster_x_positions,
    extract_text_blocks,
    _debug_print_blocks,
    _get_page_width,
)
from docroute.pipeline.quality_scorer import page_needs_ocr
from docroute.pipeline.splitter import split_pdf_pages

COLORS = ["red", "blue", "green", "orange", "purple", "brown"]


def _isolate_single_page(input_path: Path, page_number: int, tmp_dir: Path) -> Path:
    """PDFs must be split before conversion -- handing a multi-page PDF
    straight to Docling mixes every page's text blocks into one
    classify_layout() call. Non-PDF images are inherently single-page."""
    if input_path.suffix.lower() != ".pdf":
        return input_path
    page_paths = split_pdf_pages(input_path, tmp_dir)
    if not (1 <= page_number <= len(page_paths)):
        print(f"page_number {page_number} out of range -- this PDF has {len(page_paths)} page(s)")
        sys.exit(1)
    return page_paths[page_number - 1]


def _decide_ocr(page_path: Path) -> bool:
    if page_path.suffix.lower() != ".pdf":
        return True  # image input has no native text layer at all
    import pymupdf
    doc = pymupdf.open(page_path)
    native_text = doc[0].get_text()
    doc.close()
    return page_needs_ocr(native_text)


def main():
    if len(sys.argv) not in (2, 3):
        print("Usage: uv run python scripts/inspect_layout.py <file.pdf|file.png> [page_number]")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    page_number = int(sys.argv[2]) if len(sys.argv) == 3 else 1
    if not input_path.is_file():
        print(f"Not found: {input_path}")
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp:
        page_path = _isolate_single_page(input_path, page_number, Path(tmp))
        if input_path.suffix.lower() == ".pdf":
            print(f"Inspecting page {page_number} of {input_path.name} (isolated before conversion)")

        needs_ocr = _decide_ocr(page_path)
        print(f"Converting with {'forced OCR' if needs_ocr else 'native text'} converter...")
        converter = build_ocr_converter() if needs_ocr else build_native_converter()
        result = converter.convert(str(page_path))

        # Everything below stays inside the temp-dir context -- Docling's
        # page image may be loaded lazily from the converted file, so we
        # don't risk touching it after the temp file is gone.

        print()
        print("--- Debug: first blocks as extracted (verify y0 increases DOWN the page) ---")
        _debug_print_blocks(result, limit=8)
        print()

        classification = classify_layout(result)
        print("--- Classification ---")
        print(f"layout_type      = {classification.layout_type}")
        print(f"confidence       = {classification.confidence:.2f}")
        print(f"needs_escalation = {classification.needs_escalation}")
        print(f"features         = {classification.features}")
        print()

        # Pre-merge cluster view, for comparison against features['n_clusters']
        # (the post-merge count) above.
        blocks = extract_text_blocks(result)
        page_width = _get_page_width(result, blocks)
        raw_clusters = cluster_x_positions(blocks, page_width, LAYOUT_GAP_RATIO)
        print(f"Raw clusters before noise-merge: {len(raw_clusters)}")
        for i, c in enumerate(raw_clusters):
            sample = c[0].text[:40] if c else ""
            print(f"  cluster {i}: {len(c)} block(s), e.g. {sample!r}")
        print()

        try:
            page = next(iter(result.document.pages.values()))
            img = page.image.pil_image.convert("RGB") if page.image else None
        except Exception as e:
            print(f"Could not get a page image to draw on ({type(e).__name__}: {e}) -- "
                  f"skipping visualization. See caveat #3 in this script's docstring.")
            return

        if img is None:
            print("page.image is None -- confirm generate_page_images=True is set "
                  "(it already is by default in pipeline/converter.py). Skipping drawing.")
            return

        draw = ImageDraw.Draw(img)
        for i, cluster in enumerate(raw_clusters):
            color = COLORS[i % len(COLORS)]
            for b in cluster:
                draw.rectangle(
                    [b.x0 * IMAGE_RESOLUTION_SCALE, b.y0 * IMAGE_RESOLUTION_SCALE,
                     b.x1 * IMAGE_RESOLUTION_SCALE, b.y1 * IMAGE_RESOLUTION_SCALE],
                    outline=color, width=2,
                )

        suffix = f"_p{page_number}" if input_path.suffix.lower() == ".pdf" else ""
        out_path = input_path.with_name(f"{input_path.stem}{suffix}_layout_annotated.png")
        img.save(out_path)
        print(f"-> {out_path}")
        print("If the boxes don't line up with the visible text, see caveat #2 above.")


if __name__ == "__main__":
    main()
