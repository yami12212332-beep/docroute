"""
FastAPI backend for docroute.

    uvicorn docroute.api.main:app --host 0.0.0.0 --port 8000

Endpoints:
  POST /api/documents            upload a PDF, starts a background job,
                                  returns {job_id, status} immediately
  GET  /api/jobs/{job_id}         poll job status (queued/running/done/failed)
  GET  /api/jobs                  list all known jobs (dev/debug convenience)
  GET  /api/jobs/{job_id}/result  fetch the result manifest once status=done
  GET  /health                    liveness check
  /files/...                      static mount serving each job's output
                                   (merged HTML, extracted images, table
                                   CSVs, corrected-reading-order text) so
                                   a frontend can link/embed them directly

Concurrency model: one ProcessPoolExecutor, sized by env var
DOCROUTE_API_WORKERS (default 1). Each worker process loads the Docling
converters -- including the OCR model -- exactly once at startup and
reuses them for every job routed to it (see api/worker.py). Requests
never block on conversion: POST /api/documents returns as soon as the
upload is saved to disk, and the actual pipeline run happens in the pool
while the event loop stays free to serve other requests.

Raising DOCROUTE_API_WORKERS above 1 means more documents can convert in
parallel, at the cost of each additional worker process loading its own
copy of the OCR model in memory -- size it against your available RAM,
not just CPU count.

Job state (api/jobs.py) is in-memory only: restarting the API process
loses the job_id -> status mapping, though the output files already
written to disk survive. Fine for local dev / a single instance; put a
persistent store behind JobStore before running more than one API
replica, since two replicas would otherwise each have their own,
inconsistent view of which jobs exist.
"""

import asyncio
import os
import shutil
import uuid
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from docroute.api.jobs import JobStatus, job_store
from docroute.api.schemas import (
    ImageRecordOut,
    JobCreateResponse,
    JobResultResponse,
    JobStatusResponse,
)
from docroute.api.worker import init_worker, run_ingest_in_worker

JOBS_ROOT = Path(os.environ.get("DOCROUTE_JOBS_ROOT", "output/jobs")).resolve()
MAX_WORKERS = int(os.environ.get("DOCROUTE_API_WORKERS", "1"))
MAX_UPLOAD_MB = int(os.environ.get("DOCROUTE_MAX_UPLOAD_MB", "100"))
# Comma-separated list of allowed frontend origins, e.g.
# "http://localhost:5173,https://my-frontend.example.com". Defaults to
# "*" (any origin) for local development -- a frontend opened straight
# from disk (file://) sends Origin: null, and browsers don't reliably
# let you allowlist "null" explicitly, so "*" is the pragmatic default
# here. Lock this down to your real frontend's origin(s) before this API
# is reachable from anywhere untrusted.
CORS_ORIGINS = os.environ.get("DOCROUTE_CORS_ORIGINS", "*").split(",")

process_pool: ProcessPoolExecutor | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global process_pool
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    process_pool = ProcessPoolExecutor(max_workers=MAX_WORKERS, initializer=init_worker)
    yield
    process_pool.shutdown(wait=True, cancel_futures=True)


app = FastAPI(title="docroute API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,  # must be False when allow_origins includes "*" (spec/browser requirement)
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serves every job's output directory read-only, e.g.
# GET /files/<job_id>/<doc_id>.html -- mounted at startup import time
# (not inside lifespan) so route registration doesn't depend on startup
# having already run; the directory itself is created in lifespan().
JOBS_ROOT.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=str(JOBS_ROOT)), name="files")


def _to_file_url(path_str: str) -> str:
    """Converts an absolute path under JOBS_ROOT (as returned in
    ingest_pdf()'s result dict) into a /files/... URL a frontend can
    fetch directly."""
    rel = Path(path_str).resolve().relative_to(JOBS_ROOT)
    return f"/files/{rel.as_posix()}"


async def _run_job(job_id: str, pdf_path: Path, job_dir: Path) -> None:
    job_store.set_running(job_id)
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            process_pool, run_ingest_in_worker, str(pdf_path), str(job_dir)
        )
        job_store.set_done(job_id, result)
    except Exception as exc:  # noqa: BLE001 -- surface any pipeline failure to the client instead of losing it in a bare task
        job_store.set_failed(job_id, str(exc))


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/documents", response_model=JobCreateResponse, status_code=202)
async def submit_document(file: Annotated[UploadFile, File()]) -> JobCreateResponse:
    """Accepts one PDF, saves it, and schedules ingestion in the
    background. Only .pdf is accepted -- matches cli.py's SUPPORTED_EXT;
    extend both together if image-only input support is ever added."""
    original_name = file.filename or ""
    if Path(original_name).suffix.lower() != ".pdf":
        raise HTTPException(400, "Only .pdf files are supported.")

    job_id = str(uuid.uuid4())
    job_dir = JOBS_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # .name strips any directory components from the client-supplied
    # filename (path traversal defense) while keeping it human-readable,
    # since ingest_pdf() derives doc_id from this filename's stem.
    safe_name = Path(original_name).name
    pdf_path = job_dir / safe_name

    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    size = 0
    with pdf_path.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                out.close()
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(413, f"File exceeds {MAX_UPLOAD_MB} MB limit.")
            out.write(chunk)

    job_store.create(job_id=job_id, filename=safe_name, job_dir=job_dir)
    asyncio.create_task(_run_job(job_id, pdf_path, job_dir))

    return JobCreateResponse(job_id=job_id, status=JobStatus.QUEUED)


@app.get("/api/jobs", response_model=list[JobStatusResponse])
async def list_jobs() -> list[JobStatusResponse]:
    return [
        JobStatusResponse(
            job_id=j.job_id,
            status=j.status,
            filename=j.filename,
            created_at=j.created_at,
            error=j.error,
        )
        for j in job_store.list()
    ]


@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str) -> JobStatusResponse:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job.")
    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        filename=job.filename,
        created_at=job.created_at,
        error=job.error,
    )


@app.get("/api/jobs/{job_id}/result", response_model=JobResultResponse)
async def get_job_result(job_id: str) -> JobResultResponse:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job.")
    if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        raise HTTPException(
            409, f"Job is still {job.status.value}; poll GET /api/jobs/{job_id} first."
        )
    if job.status == JobStatus.FAILED:
        raise HTTPException(500, f"Job failed: {job.error}")

    result = job.result or {}
    images = [
        ImageRecordOut(
            page_number=rec.page_number,
            image_hash=rec.image_hash,
            url=_to_file_url(rec.storage_uri),
        )
        for rec in result.get("images", [])
    ]
    return JobResultResponse(
        job_id=job.job_id,
        doc_id=result["doc_id"],
        status=job.status,
        pages=result["pages"],
        ocr_pages=result["ocr_pages"],
        html_url=_to_file_url(result["html_path"]),
        images=images,
        corrected_text_urls=[_to_file_url(p) for p in result["corrected_text_paths"]],
        escalation_pages=result["escalation_pages"],
    )
