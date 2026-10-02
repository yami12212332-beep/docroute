"""
In-memory job tracking for the API.

This is intentionally simple: a plain dict, with no lock. That's safe
here specifically because every mutation happens on the asyncio event
loop thread -- FastAPI's request handlers and the `await
loop.run_in_executor(...)` continuation in main.py's `_run_job` both run
there, never on a separate thread. If you outgrow a single API process
(need horizontal scaling, or job state to survive an API restart), swap
this for a real backing store (Redis, Postgres) behind the same
JobStore interface -- main.py only calls the methods below, so the swap
shouldn't ripple through the route handlers.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Job:
    job_id: str
    filename: str
    job_dir: Path
    status: JobStatus = JobStatus.QUEUED
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    error: str | None = None
    result: dict[str, Any] | None = None


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self, job_id: str, filename: str, job_dir: Path) -> Job:
        job = Job(job_id=job_id, filename=filename, job_dir=job_dir)
        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return list(self._jobs.values())

    def set_running(self, job_id: str) -> None:
        self._jobs[job_id].status = JobStatus.RUNNING

    def set_done(self, job_id: str, result: dict[str, Any]) -> None:
        job = self._jobs[job_id]
        job.status = JobStatus.DONE
        job.result = result

    def set_failed(self, job_id: str, error: str) -> None:
        job = self._jobs[job_id]
        job.status = JobStatus.FAILED
        job.error = error


# Single shared instance -- imported by main.py. Fine for one API
# process; see the module docstring for what to do beyond that.
job_store = JobStore()
