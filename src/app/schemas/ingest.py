from pydantic import BaseModel, Field


class ScrapeRequest(BaseModel):
    sport: str = Field(..., description="Sport key to fetch odds for (e.g., 'football').")


class IngestUploadResponse(BaseModel):
    job_id: int
    records: int
    status: str


class IngestScrapeResponse(BaseModel):
    job_id: int
    status: str
    detail: str
