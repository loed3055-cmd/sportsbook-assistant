import json
import logging
from datetime import datetime, timezone
from io import StringIO
from typing import Any, Iterable

import pandas as pd
from sqlalchemy.orm import Session

from app.models.odds import Odds

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def fetch_odds_from_api(sport: str) -> list[dict]:
    """
    Mock fetching odds for a sport, mimicking The Odds API response shape.
    """

    now = datetime.now(timezone.utc)
    return [
        {
            "id": f"mock-{sport}-001",
            "sport": sport,
            "league": "Mock League",
            "home_team": "Mock Home",
            "away_team": "Mock Away",
            "start_time": now.isoformat(),
            "markets": [
                {
                    "sportsbook": "The Odds API (Mock)",
                    "market_type": "moneyline",
                    "description": "Moneyline (home)",
                    "outcome": "home",
                    "price_decimal": 1.92,
                },
                {
                    "sportsbook": "The Odds API (Mock)",
                    "market_type": "spread",
                    "description": "Spread +3.5",
                    "outcome": "away",
                    "price_decimal": 1.98,
                },
            ],
        }
    ]


def normalize_odds(raw_data: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Normalize heterogeneous odds payloads into the canonical schema.
    """

    normalized: list[dict[str, Any]] = []

    def _parse_timestamp(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        timestamp = str(value)
        if timestamp.endswith("Z"):
            timestamp = timestamp.replace("Z", "+00:00")
        return datetime.fromisoformat(timestamp).astimezone(timezone.utc)

    for event in raw_data:
        event_timestamp = (
            event.get("event_timestamp") or event.get("start_time") or datetime.now(timezone.utc)
        )
        timestamp = _parse_timestamp(event_timestamp)
        base = {
            "sport": event.get("sport", "unknown"),
            "league": event.get("league", "unknown"),
            "team_home": event.get("home_team") or event.get("team_home") or "",
            "team_away": event.get("away_team") or event.get("team_away") or "",
            "event_timestamp": timestamp,
            "external_event_id": event.get("id") or event.get("event_id"),
        }
        markets = event.get("markets") or [event]
        if not isinstance(markets, list):
            markets = [markets]
        for market in markets:
            price = market.get("price_decimal") or market.get("price")
            if price is None:
                continue
            normalized_record = {
                **base,
                "sportsbook": market.get("sportsbook") or market.get("provider") or "mock",
                "market_type": market.get("market_type") or market.get("market") or "unknown",
                "market_description": market.get("description") or market.get("market_type") or "unknown",
                "outcome": market.get("outcome") or market.get("selection") or "unknown",
                "price_decimal": float(price),
            }
            normalized.append(normalized_record)

    logger.debug("Normalized %d odds records", len(normalized))
    return normalized


def insert_odds_to_db(session: Session, normalized_records: list[dict[str, Any]]) -> int:
    """
    Bulk insert normalized odds into the database, returning the count inserted.
    """

    if not normalized_records:
        return 0

    models = [Odds(**record) for record in normalized_records]
    session.add_all(models)
    session.commit()
    logger.info("Inserted %d odds rows", len(models))
    return len(models)


def parse_uploaded_file(content: bytes, filename: str, content_type: str | None) -> list[dict[str, Any]]:
    """
    Parse a CSV or JSON payload coming from an upload and return record dicts.
    """

    text = content.decode("utf-8")
    if filename.lower().endswith(".json") or (content_type and "json" in content_type):
        payload = json.loads(text)
        if isinstance(payload, dict):
            payload = payload.get("records") or payload.get("data") or [payload]
        return payload if isinstance(payload, list) else [payload]

    df = pd.read_csv(StringIO(text))
    return df.to_dict(orient="records")
