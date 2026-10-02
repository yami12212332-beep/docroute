# docroute

Per-page adaptive OCR pipeline built on Docling.

Decides, **per page** rather than per document, whether to trust a PDF's
native text layer or discard it and force full-page OCR — so a document
with 10 typed pages and 2 scanned/corrupted pages gets each page handled
correctly instead of one global mode being wrong for half the document.

## Why per-page

Docling's default "auto" mode trusts any existing text layer, even a
corrupted one from a scanner's own partial OCR. Forcing OCR on every page
fixes that but throws away perfectly good native text on typed pages and
is unreliable on long documents (known Docling bug class around
`force_full_page_ocr` degrading past the first several pages — see
`pipeline/router.py` docstring). Splitting and routing per page avoids
both problems.

## How a page is classified

`pipeline/quality_scorer.py` scores each page's native text (no OCR run
yet — this uses text PyMuPDF already extracted) on:

- printable-character ratio
- alphanumeric-vs-symbol ratio
- common English function-word hit rate (strongest signal)
- hard-fail markers (`GLYPH<`, `\ufffd`)

Pages scoring below `QUALITY_SCORE_THRESHOLD` (see `config.py`) are routed
to forced full-page OCR; everything else keeps native text. **This
threshold needs tuning against your own real documents** — it ships as a
starting point, not a validated constant.

## Install

```bash
uv sync
```

## Run

```bash
uv run docroute path/to/file.pdf
uv run docroute path/to/folder/
```

Outputs land in `output/`: merged HTML per document, one CSV per detected
table, and extracted images (hash-deduped) under `output/images/`.

## Test

```bash
uv run pytest
```

Drop sample PDFs (ideally a mix of clean-typed, clean-scanned, and
partially-corrupted pages) into `tests/fixtures/` as you tune the scorer.

## Project layout

See module docstrings — `pipeline/router.py` is the orchestration entry
point if you want to trace the full flow; `pipeline/quality_scorer.py` and
`config.py` are the two files you'll actually be tuning day to day.

## Layout classification (reading-order correctness)

Separate problem from OCR-vs-native: even when the text is correct,
Docling's column-based reading order can be *wrong* for certain layouts.
A "label:value" form (narrow labels paired row-by-row with wider
descriptive text -- e.g. `"TREATMENT GIVEN : IV Fluids..."`) gets
misread by Docling's default ordering as "every label, then every value",
because it looks geometrically like two columns.

`pipeline/layout_classifier.py` runs on every converted page and
classifies it, from bounding boxes alone (no model call), as one of:

- `single_column` / `parallel_columns` — Docling's own ordering is trusted
- `label_value_form` — Docling's ordering is wrong; `pipeline/reading_order.py`
  reconstructs correct row-by-row order instead (written to a separate
  `_corrected.txt` file per page — the HTML output still reflects Docling's
  order, since the override only rebuilds plain text, not full HTML structure)
- `ambiguous` — the classifier can't confidently place the page (e.g. more
  than two x-position clusters). Flagged `needs_escalation=True` and
  printed as a warning. **Tier 2 (sending the page image to a vision
  model for a real judgment call) is not implemented yet** — these pages
  currently fall back to Docling's unverified default order.

**Before trusting this in production:** the coordinate-origin handling in
`layout_classifier.py` (`_normalize_bbox_to_top_left`) has not been
empirically verified against your installed Docling version's actual
bounding-box output. Run `_debug_print_blocks()` from that module against
a real converted page first and confirm y0 increases going *down* the
page before relying on any classification result. All thresholds in
`config.py`'s layout section are unvalidated starting points, same
caveat as `QUALITY_SCORE_THRESHOLD`.

## Test the layout classifier against a real document

```bash
uv run python scripts/inspect_layout.py path/to/page.pdf
```

Runs the real Docling pipeline (not the raw-OCR sandbox demo used during
development) against one page, prints the classification decision and
raw features, and writes `<name>_layout_annotated.png` with each detected
cluster drawn in a different color so you can check it against the page
visually. Read the caveats in the script's own docstring before trusting
the drawing -- coordinate origin and pixel scaling are both
best-effort/unverified against your exact installed Docling version.

## API (backend for a frontend)

`src/docroute/api/` wraps `ingestion/ingest.py` in a FastAPI service so a
frontend can upload a PDF, poll for completion, and fetch results over
HTTP instead of shelling out to the CLI.

**Not yet run end-to-end** — written against the pipeline's actual
function signatures (verified by reading the source), but not executed
here for lack of a network/Docling install in the environment it was
written in. Test it yourself with the steps below before relying on it;
don't take "it compiles" as "it works."

### Install and run

```bash
uv pip install -e ".[api]"     # adds fastapi, uvicorn, python-multipart
uvicorn docroute.api.main:app --host 0.0.0.0 --port 8000
```

Env vars (all optional):

| Var | Default | Meaning |
|---|---|---|
| `DOCROUTE_JOBS_ROOT` | `output/jobs` | where per-job output lands and is served from |
| `DOCROUTE_API_WORKERS` | `1` | ProcessPoolExecutor size — each worker loads its own copy of the OCR model, so size against RAM, not just CPU count |
| `DOCROUTE_MAX_UPLOAD_MB` | `100` | reject uploads larger than this |

### Try it

```bash
# 1. submit a PDF
curl -F "file=@some_document.pdf" http://localhost:8000/api/documents
# -> {"job_id": "...", "status": "queued"}

# 2. poll until status is "done" or "failed"
curl http://localhost:8000/api/jobs/<job_id>

# 3. once done, fetch the result manifest
curl http://localhost:8000/api/jobs/<job_id>/result
# -> html_url, per-page image URLs, corrected-text URLs, escalation_pages

# the URLs above are served directly, e.g.:
curl http://localhost:8000/files/<job_id>/<doc_id>.html
```

Interactive API docs (Swagger UI) are auto-served at `/docs` once the
server is running — useful for exploring/testing from a browser before
wiring up the actual frontend.

### Design notes / what a frontend integrator should know

- **Async job model.** Upload returns immediately with a `job_id`; the
  actual Docling conversion runs in a background process pool. Poll
  `GET /api/jobs/{job_id}` until `status` is `done` or `failed`, then hit
  `/result`.
- **One converter load per worker process, not per document.**
  `route_document()` / `ingest_pdf()` in the base pipeline both default
  to building fresh Docling converters per call (fine for the CLI, which
  only ever runs once). The API instead builds them once per worker
  process (`api/worker.py`'s `init_worker`) and passes them in — the
  underlying functions gained optional `native_converter`/`ocr_converter`
  params for exactly this, and default to the old behavior if omitted.
- **Per-job output isolation.** `save_image()` gained an optional
  `out_dir` param and `ingest_pdf()` an optional `output_dir` param, both
  defaulting to the old global `config.py` paths. The API passes a
  per-job directory (`output/jobs/<job_id>/`) so concurrent uploads never
  write into the same folder.
- **Job state is in-memory (`api/jobs.py`).** Restarting the API process
  loses the job_id → status mapping (the output files on disk survive).
  Fine for local dev / one instance; put a real store (Redis, Postgres)
  behind `JobStore` before running more than one API replica.
- **Escalation pages still aren't corrected.** `escalation_pages` in the
  result manifest is passed straight through from the pipeline — Tier 2
  vision-model correction still isn't implemented (see
  `pipeline/router.py`), so a frontend should visibly flag those pages
  as "reading order unverified," not silently trust them.

## Known limitations / open items

- Image inputs (`.png`/`.jpg`) aren't wired into the per-page router yet —
  `cli.py` currently only accepts `.pdf`.
- Image storage is local-disk only (`images/storage.py`); swap
  `save_image` for an S3/GCS upload when moving to continuous ingestion.
- No captioning/embedding step yet for extracted images — `ImageRecord`
  has the fields (`caption`, `embedding`) reserved for that.
- `merge_pages_html` is a naive concatenation — replace with a real
  `DoclingDocument` merge if downstream needs one coherent document object
  rather than per-page HTML.
- Tier 2 layout escalation (vision-model check for `ambiguous` pages) is
  not implemented — see `pipeline/router.py` docstring.
- `label_value_form` correction only produces corrected plain text, not
  corrected HTML — chunking/embedding logic downstream needs to prefer
  the `_corrected.txt` file over the `.html` for those specific pages.
