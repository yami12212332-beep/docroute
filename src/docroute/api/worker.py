"""
ProcessPoolExecutor worker-side code.

Each worker process builds its own native/OCR Docling converters exactly
ONCE, at process startup (`init_worker`, passed to the pool as its
`initializer`), and reuses them for every job routed to that process
afterwards. This matters because pipeline/converter.py's converters load
the OCR model -- doing that per-document instead of per-process would
make every API request pay a model-load cost that route_document()'s
CLI-era default (build fresh converters per call) was never designed to
avoid, since the CLI only ever processes one call before exiting anyway.

`_native_converter` / `_ocr_converter` are process-local globals: each
worker process gets its own copy (they're never pickled or shared across
processes), set once here and read by run_ingest_in_worker() below.
"""

from pathlib import Path

from docling.document_converter import DocumentConverter

from docroute.ingestion.ingest import ingest_pdf
from docroute.pipeline.converter import build_native_converter, build_ocr_converter

_native_converter: DocumentConverter | None = None
_ocr_converter: DocumentConverter | None = None


def init_worker() -> None:
    """Runs once per worker process when the ProcessPoolExecutor starts
    it (passed as `initializer=init_worker` in main.py). Loads both
    Docling converters up front so every job this process later handles
    reuses them instead of rebuilding."""
    global _native_converter, _ocr_converter
    _native_converter = build_native_converter()
    _ocr_converter = build_ocr_converter()


def run_ingest_in_worker(pdf_path: str, output_dir: str) -> dict:
    """The actual unit of work submitted to the pool. Runs in a worker
    process, using that process's pre-built converters. Falls back to
    ingest_pdf()'s own defaults (build fresh converters) if somehow
    called before init_worker() ran -- shouldn't happen via the API, but
    keeps this function safe to call directly too, e.g. from a script or
    a test, without requiring the ProcessPoolExecutor machinery."""
    return ingest_pdf(
        Path(pdf_path),
        output_dir=Path(output_dir),
        native_converter=_native_converter,
        ocr_converter=_ocr_converter,
    )
