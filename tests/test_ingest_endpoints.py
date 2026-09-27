from http import HTTPStatus
import time

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.job import Job
from app.models.odds import Odds

CSV_PAYLOAD = """sportsbook,sport,league,market_type,market_description,outcome,price_decimal,event_timestamp,team_home,team_away,external_event_id
MockBook,football,NFL,moneyline,Home moneyline,home,1.85,2024-10-01T18:00:00Z,Mock Home,Mock Away,upload-1
"""


def test_upload_endpoint_creates_job_and_odds(client, db_session: Session) -> None:
    response = client.post(
        "/ingest/upload",
        files={"file": ("odds.csv", CSV_PAYLOAD, "text/csv")},
    )

    assert response.status_code == HTTPStatus.OK
    payload = response.json()
    assert payload["records"] == 1
    assert payload["status"] == "completed"

    job = db_session.get(Job, payload["job_id"])
    assert job is not None
    assert job.records_ingested == 1
    assert job.status == "completed"

    odds_count = db_session.query(Odds).count()
    assert odds_count == 1


def test_scrape_endpoint_queues_background_job(client) -> None:
    response = client.post("/ingest/scrape", json={"sport": "football"})
    assert response.status_code == HTTPStatus.OK
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["detail"] == "Scrape job queued"

    time.sleep(0.3)
    with SessionLocal() as verification_session:
        job = verification_session.get(Job, payload["job_id"])
        assert job is not None
        assert job.status == "completed"
        assert job.records_ingested == 2
        odds_count = verification_session.query(Odds).count()
        assert odds_count >= job.records_ingested


def test_ingest_scheduler_can_restart_and_run_jobs() -> None:
    """start -> shutdown -> start must yield a scheduler with a live executor."""

    import threading

    from app.routers import ingest

    ingest.shutdown_ingest_scheduler()  # ensure a clean state regardless of test order
    ingest.start_ingest_scheduler()
    ingest.shutdown_ingest_scheduler()
    ingest.start_ingest_scheduler()
    try:
        ran = threading.Event()
        ingest.scheduler.add_job(ran.set, id="restart-probe", replace_existing=True)
        assert ran.wait(timeout=2), "job did not run after scheduler restart"
    finally:
        ingest.shutdown_ingest_scheduler()
