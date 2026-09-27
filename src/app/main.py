from fastapi import FastAPI
from app.db import Base, engine
from app.routers import predictions, teams
from app.routers.ingest import (
    router as ingest_router,
    shutdown_ingest_scheduler,
    start_ingest_scheduler,
)

# Create tables automatically for now (later use Alembic)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Sportsbook Assistant MVP")

app.include_router(teams.router, prefix="/teams", tags=["teams"])
app.include_router(ingest_router, prefix="/ingest", tags=["ingest"])
app.include_router(predictions.router, prefix="/predictions", tags=["predictions"])


@app.on_event("startup")
def _start_scheduler() -> None:
    """Ensure the ingestion scheduler is running when the API boots."""

    start_ingest_scheduler()


@app.on_event("shutdown")
def _stop_scheduler() -> None:
    """Gracefully shut down background workers."""

    shutdown_ingest_scheduler()


@app.get("/")
def root():
    """Health check for the FastAPI layer that fronts the ETL, analytics, and ingestion subsystems."""

    return {"message": "Sportsbook Assistant API is live 🚀"}
