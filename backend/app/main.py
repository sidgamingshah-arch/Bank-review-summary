"""FastAPI entrypoint. Run with:  uvicorn app.main:app --reload  (from backend/)."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from asm_review.config import get_settings
from app.jobs import JobStore

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="ASM Review Note Generator", version="0.1.0")
store = JobStore(get_settings().data_dir)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/jobs")
async def create_job(files: list[UploadFile] = File(...)) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one source document.")

    job, job_dir = store.create()
    inputs_dir = job_dir / "inputs"
    saved: list[str] = []
    for upload in files:
        name = Path(upload.filename or "file").name
        dest = inputs_dir / name
        with dest.open("wb") as out:
            shutil.copyfileobj(upload.file, out)
        saved.append(str(dest))

    store.submit(job.id, saved)
    return {"job_id": job.id, "status": job.status}


@app.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id.")
    return job.public()


@app.get("/jobs/{job_id}/document")
def job_document(job_id: str) -> FileResponse:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id.")
    if job.status != "done" or not job.docx_path:
        raise HTTPException(status_code=409, detail=f"Document not ready (status={job.status}).")
    path = Path(job.docx_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Generated document is missing.")
    return FileResponse(
        path,
        media_type=DOCX_MIME,
        filename=f"ASM_Review_Note_{job_id}.docx",
    )
