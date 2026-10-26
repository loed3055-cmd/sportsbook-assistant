import pytest

from app.models.odds import Odds
from app.services.etl import fetch_odds_from_api, insert_odds_to_db, normalize_odds


def test_fetch_odds_from_api_returns_structured_payload() -> None:
    data = fetch_odds_from_api("football")
    assert isinstance(data, list)
    assert data and data[0]["sport"] == "football"
    assert "markets" in data[0]


@pytest.mark.asyncio
async def test_normalize_odds_handles_market_lists() -> None:
    raw_data = fetch_odds_from_api("basketball")
    normalized = normalize_odds(raw_data)
    assert normalized
    assert all("market_type" in record for record in normalized)
    assert all(isinstance(record["price_decimal"], float) for record in normalized)


def test_insert_odds_to_db_populates_table(db_session) -> None:
    normalized = normalize_odds(fetch_odds_from_api("football"))
    inserted = insert_odds_to_db(db_session, normalized)
    assert inserted == len(normalized)
    assert db_session.query(Odds).count() == inserted
