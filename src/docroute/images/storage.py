"""
Stores extracted images and their metadata.

Local-disk backend for now (matches where the pipeline currently runs).
Swap `save_image` for an S3/GCS upload when you move to continuous
ingestion -- the metadata dict shape stays the same either way, so
whatever indexes this (vector DB row, Postgres table) doesn't need to
change when the storage backend does.
"""

import hashlib
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from docroute.config import IMAGE_OUTPUT_DIR


@dataclass
class ImageRecord:
    """What you'd write as a row/document once this feeds a vector DB.
    caption and embedding are left None here -- filled in by a later
    captioning/embedding step, not this module's job."""
    doc_id: str
    page_number: int
    image_hash: str
    storage_uri: str
    caption: str | None = None
    embedding: list[float] | None = None
    ingested_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _hash_image(image: Image.Image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return hashlib.sha256(buf.getvalue()).hexdigest()


def save_image(
    image: Image.Image,
    doc_id: str,
    page_number: int,
    ref: str,
    out_dir: Path | None = None,
) -> ImageRecord:
    """Saves one image to local disk, deduped by content hash so the same
    logo/stamp repeated across many pages/documents isn't stored twice.

    out_dir defaults to config.IMAGE_OUTPUT_DIR (old behavior, used by the
    CLI). Pass an explicit out_dir (e.g. a per-job directory) to avoid
    concurrent callers -- like an API processing multiple documents at
    once -- writing into the same shared folder."""
    out_dir = Path(out_dir) if out_dir is not None else Path(IMAGE_OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    image_hash = _hash_image(image)
    out_path = out_dir / f"{image_hash}.png"

    if not out_path.exists():  # dedup -- identical bytes already on disk
        image.save(out_path)

    return ImageRecord(
        doc_id=doc_id,
        page_number=page_number,
        image_hash=image_hash,
        storage_uri=str(out_path),
    )
