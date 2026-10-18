"""Where each game is played: NFL home stadiums plus international neutral sites.

Pure reference data (no DB/framework imports), keyed the same way as
``services/grading.py``: team codes go through ``normalize_team`` first.
Coordinates are the stadium itself, so forecasts are for the field, not the
city center. ``roof`` decides whether weather matters:

- ``open``: forecast it.
- ``retractable``: forecast it, but the roof may be closed on game day.
- ``dome``: fixed roof (including SoFi's canopy); weather is irrelevant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.grading import parse_matchup

Roof = Literal["open", "retractable", "dome"]


@dataclass(frozen=True)
class Venue:
    name: str
    city: str
    country: str  # ISO 3166-1 alpha-2; "US" venues use NWS, others Open-Meteo
    lat: float
    lon: float
    roof: Roof


_METLIFE = Venue("MetLife Stadium", "East Rutherford, NJ", "US", 40.8135, -74.0745, "open")
_SOFI = Venue("SoFi Stadium", "Inglewood, CA", "US", 33.9535, -118.3392, "dome")

STADIUMS: dict[str, Venue] = {
    "ARI": Venue("State Farm Stadium", "Glendale, AZ", "US", 33.5276, -112.2626, "retractable"),
    "ATL": Venue("Mercedes-Benz Stadium", "Atlanta, GA", "US", 33.7554, -84.4008, "retractable"),
    "BAL": Venue("M&T Bank Stadium", "Baltimore, MD", "US", 39.2780, -76.6227, "open"),
    "BUF": Venue("Highmark Stadium", "Orchard Park, NY", "US", 42.7738, -78.7870, "open"),
    "CAR": Venue("Bank of America Stadium", "Charlotte, NC", "US", 35.2258, -80.8528, "open"),
    "CHI": Venue("Soldier Field", "Chicago, IL", "US", 41.8623, -87.6167, "open"),
    "CIN": Venue("Paycor Stadium", "Cincinnati, OH", "US", 39.0955, -84.5161, "open"),
    "CLE": Venue("Huntington Bank Field", "Cleveland, OH", "US", 41.5061, -81.6995, "open"),
    "DAL": Venue("AT&T Stadium", "Arlington, TX", "US", 32.7473, -97.0945, "retractable"),
    "DEN": Venue("Empower Field at Mile High", "Denver, CO", "US", 39.7439, -105.0201, "open"),
    "DET": Venue("Ford Field", "Detroit, MI", "US", 42.3400, -83.0456, "dome"),
    "GB": Venue("Lambeau Field", "Green Bay, WI", "US", 44.5013, -88.0622, "open"),
    "HOU": Venue("NRG Stadium", "Houston, TX", "US", 29.6847, -95.4107, "retractable"),
    "IND": Venue("Lucas Oil Stadium", "Indianapolis, IN", "US", 39.7601, -86.1639, "retractable"),
    "JAX": Venue("EverBank Stadium", "Jacksonville, FL", "US", 30.3239, -81.6373, "open"),
    "KC": Venue("GEHA Field at Arrowhead Stadium", "Kansas City, MO", "US", 39.0489, -94.4839, "open"),
    "LV": Venue("Allegiant Stadium", "Paradise, NV", "US", 36.0909, -115.1833, "dome"),
    "LAC": _SOFI,
    "LAR": _SOFI,
    "MIA": Venue("Hard Rock Stadium", "Miami Gardens, FL", "US", 25.9580, -80.2389, "open"),
    "MIN": Venue("U.S. Bank Stadium", "Minneapolis, MN", "US", 44.9737, -93.2575, "dome"),
    "NE": Venue("Gillette Stadium", "Foxborough, MA", "US", 42.0909, -71.2643, "open"),
    "NO": Venue("Caesars Superdome", "New Orleans, LA", "US", 29.9511, -90.0812, "dome"),
    "NYG": _METLIFE,
    "NYJ": _METLIFE,
    "PHI": Venue("Lincoln Financial Field", "Philadelphia, PA", "US", 39.9008, -75.1675, "open"),
    "PIT": Venue("Acrisure Stadium", "Pittsburgh, PA", "US", 40.4468, -80.0158, "open"),
    "SF": Venue("Levi's Stadium", "Santa Clara, CA", "US", 37.4030, -121.9700, "open"),
    "SEA": Venue("Lumen Field", "Seattle, WA", "US", 47.5952, -122.3316, "open"),
    "TB": Venue("Raymond James Stadium", "Tampa, FL", "US", 27.9759, -82.5033, "open"),
    "TEN": Venue("Nissan Stadium", "Nashville, TN", "US", 36.1665, -86.7713, "open"),
    "WSH": Venue("Northwest Stadium", "Landover, MD", "US", 38.9076, -76.8645, "open"),
}

# International Series and other neutral sites, by slug (pass as ``venue``).
NEUTRAL_VENUES: dict[str, Venue] = {
    "tottenham": Venue("Tottenham Hotspur Stadium", "London", "GB", 51.6043, -0.0664, "open"),
    "wembley": Venue("Wembley Stadium", "London", "GB", 51.5560, -0.2796, "open"),
    "dublin": Venue("Croke Park", "Dublin", "IE", 53.3607, -6.2512, "open"),
    "munich": Venue("Allianz Arena", "Munich", "DE", 48.2188, 11.6247, "open"),
    "frankfurt": Venue("Deutsche Bank Park", "Frankfurt", "DE", 50.0686, 8.6455, "retractable"),
    "berlin": Venue("Olympiastadion", "Berlin", "DE", 52.5147, 13.2395, "open"),
    "paris": Venue("Stade de France", "Saint-Denis", "FR", 48.9245, 2.3602, "open"),
    "madrid": Venue("Santiago Bernabéu", "Madrid", "ES", 40.4531, -3.6883, "retractable"),
    "sao-paulo": Venue("Neo Química Arena", "São Paulo", "BR", -23.5453, -46.4742, "open"),
    "rio": Venue("Maracanã", "Rio de Janeiro", "BR", -22.9122, -43.2302, "open"),
    "mexico-city": Venue("Estadio Banorte", "Mexico City", "MX", 19.3029, -99.1505, "open"),
    "melbourne": Venue("Melbourne Cricket Ground", "Melbourne", "AU", -37.8200, 144.9834, "open"),
}


def venue_for(matchup: str, venue: str | None = None) -> Venue:
    """The home team's stadium, or the neutral site named by ``venue`` (a slug).

    Raises ValueError for an unknown home team or venue slug.
    """

    if venue is not None:
        slug = venue.strip().lower()
        if slug not in NEUTRAL_VENUES:
            raise ValueError(f"unknown venue {venue!r}; known: {sorted(NEUTRAL_VENUES)}")
        return NEUTRAL_VENUES[slug]
    _away, home = parse_matchup(matchup)
    if home not in STADIUMS:
        raise ValueError(f"no stadium for home team {home!r}")
    return STADIUMS[home]
