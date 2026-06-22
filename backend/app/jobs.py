"""In-memory job store that runs the (blocking) pipeline in a worker thread.

Sufficient for a single-process v1. For multi-worker / durable deployment, swap
this for a real queue + datastore; the pipeline itself is stateless.
"""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Optional

from asm_review.config import get_settings
from asm_review.pipeline import run_pipeline

logger = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    status: str = "queued"  # queued | processing | done | error
    stage: str = ""
    progress: float = 0.0
    doc_names: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    qc: Optional[dict] = None
    usage: Optional[dict] = None
    error: Optional[str] = None
    docx_path: Optional[str] = None

    def public(self) -> dict:
        data = asdict(self)
        data.pop("docx_path", None)  # internal
        data["download_ready"] = self.status == "done" and bool(self.docx_path)
        return data


class JobStore:
    def __init__(self, data_dir: str, max_workers: int = 2) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers)

    def create(self) -> tuple[Job, Path]:
        job_id = uuid.uuid4().hex[:12]
        job = Job(id=job_id)
        with self._lock:
            self._jobs[job_id] = job
        job_dir = self.data_dir / job_id
        (job_dir / "inputs").mkdir(parents=True, exist_ok=True)
        return job, job_dir

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def submit(self, job_id: str, input_paths: list[str]) -> None:
        self._pool.submit(self._run, job_id, input_paths)

    def _run(self, job_id: str, input_paths: list[str]) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.status = "processing"
        job.stage = "ingesting"

        def progress_cb(attr: str, idx: int, total: int) -> None:
            job.stage = f"extracting: {attr} ({idx}/{total})"
            job.progress = round(idx / total * 100, 1)

        try:
            settings = get_settings()
            output_path = self.data_dir / job_id / "ASM_Review_Note.docx"
            result = run_pipeline(
                input_paths,
                output_path,
                settings=settings,
                progress_cb=progress_cb,
            )
            job.doc_names = result.doc_names
            job.skipped = result.skipped
            job.qc = result.qc.as_dict()
            job.usage = result.usage
            job.docx_path = str(result.docx_path)
            job.stage = "done"
            job.progress = 100.0
            job.status = "done"
        except Exception as exc:  # surface failure to the client
            logger.exception("job %s failed", job_id)
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
