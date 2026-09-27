from datetime import datetime, timezone
from typing import Final

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

import app.db as app_db
from app.db import get_db
from app.models.job import Job
from app.services.etl import (
    fetch_odds_from_api,
    insert_odds_to_db,
    normalize_odds,
    parse_uploaded_file,
)
from app.schemas.ingest import IngestScrapeResponse, IngestUploadResponse, ScrapeRequest

router = APIRouter()

logger = logging.getLogger(__name__)


def _build_scheduler() -> BackgroundScheduler:
    """Create a scheduler with its own executor pool."""

    return BackgroundScheduler(timezone="UTC")


scheduler = _build_scheduler()
_scheduler_started = False
_scheduler_was_shut_down = False

JOB_SOURCE_UPLOAD: Final[str] = "upload"
JOB_SOURCE_SCRAPE: Final[str] = "scrape"


def start_ingest_scheduler() -> None:
    """Optimistically start the APScheduler instance (no-op if already running).

    A scheduler that has been shut down keeps its dead executor, so after a
    shutdown a fresh one is built; restarts in the same process then work.
    """

    global _scheduler_started, _scheduler_was_shut_down, scheduler
    if not _scheduler_started:
        if _scheduler_was_shut_down:
            scheduler = _build_scheduler()
            _scheduler_was_shut_down = False
        scheduler.start()
        _scheduler_started = True


def shutdown_ingest_scheduler() -> None:
    """Shuts down the APScheduler so PID exits cleanly during tests/deploy."""

    global _scheduler_started, _scheduler_was_shut_down
    if _scheduler_started:
        scheduler.shutdown(wait=False)
        _scheduler_started = False
        _scheduler_was_shut_down = True


def schedule_scrape_job(job_id: int, sport: str) -> None:
    """Queue an ingestion job in the background scheduler."""

    scheduler.add_job(
        run_scrape_job,
        args=[job_id, sport],
        id=f"scrape-{job_id}",
        next_run_time=datetime.now(timezone.utc),
        replace_existing=True,
    )


def run_scrape_job(job_id: int, sport: str) -> None:
    """Background worker that fetches odds, normalizes, and inserts them."""

    session = app_db.SessionLocal()
    try:
        job = session.get(Job, job_id)
        if not job:
            return

        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        session.add(job)
        session.commit()

        raw_data = fetch_odds_from_api(sport)
        normalized = normalize_odds(raw_data)
        records = insert_odds_to_db(session, normalized)

        job.records_ingested = records
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        job.message = f"ingested {records} rows"
        session.add(job)
        session.commit()
    except Exception as exc:  # pragma: no cover - best effort
        session.rollback()
        job = session.get(Job, job_id)
        if job:
            job.status = "failed"
            job.message = str(exc)
            job.completed_at = datetime.now(timezone.utc)
            session.add(job)
            session.commit()
        logger.exception("Scrape job %s failed", job_id, exc_info=exc)
    finally:
        session.close()


@router.post("/upload", response_model=IngestUploadResponse)
async def ingest_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Accept a CSV/JSON payload, normalize the rows, and persist odds in the DB."""

    content = await file.read()
    raw_records = parse_uploaded_file(content, file.filename, file.content_type)
    sport_tag = raw_records[0].get("sport", "uploaded") if raw_records else "uploaded"
    job = Job(sport=sport_tag, source=JOB_SOURCE_UPLOAD, status="running", started_at=datetime.now(timezone.utc))
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        normalized = normalize_odds(raw_records)
        inserted = insert_odds_to_db(db, normalized)
        job.records_ingested = inserted
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        job.message = f"uploaded {inserted} rows"
        db.add(job)
        db.commit()
        return IngestUploadResponse(job_id=job.id, records=inserted, status=job.status)
    except Exception as exc:
        db.rollback()
        job.status = "failed"
        job.message = str(exc)
        job.completed_at = datetime.now(timezone.utc)
        db.add(job)
        db.commit()
        logger.exception("Upload job %s failed", job.id if job else "unknown", exc_info=exc)
        raise HTTPException(status_code=500, detail="Failed to process upload")


@router.post("/scrape", response_model=IngestScrapeResponse)
def ingest_scrape(request: ScrapeRequest, db: Session = Depends(get_db)):
    """Queue a background scrape job that normalizes odds from the mock API."""

    job = Job(sport=request.sport, source=JOB_SOURCE_SCRAPE, status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        schedule_scrape_job(job.id, request.sport)
    except Exception:
        db.rollback()
        job.status = "failed"
        job.message = "could not queue job"
        job.completed_at = datetime.now(timezone.utc)
        db.add(job)
        db.commit()
        logger.exception("Could not queue scrape job %s", job.id)
        raise HTTPException(status_code=500, detail="Failed to queue scrape job")

    return IngestScrapeResponse(job_id=job.id, status=job.status, detail="Scrape job queued")
