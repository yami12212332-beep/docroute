"""
Pydantic response models for the API.

Kept separate from jobs.py's internal Job dataclass on purpose: the wire
format can then evolve independently of internal job-tracking state, and
we never accidentally serialize something internal (e.g. job_dir, a
filesystem path) out to a client.
"""

from pydantic import BaseModel

from docroute.api.jobs import JobStatus


class JobCreateResponse(BaseModel):
    job_id: str
    status: JobStatus


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    filename: str
    created_at: str
    error: str | None = None


class ImageRecordOut(BaseModel):
    page_number: int
    image_hash: str
    url: str


class JobResultResponse(BaseModel):
    job_id: str
    doc_id: str
    status: JobStatus
    pages: int
    ocr_pages: int
    html_url: str
    images: list[ImageRecordOut]
    corrected_text_urls: list[str]
    escalation_pages: list[int]
