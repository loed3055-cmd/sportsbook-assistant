"""Pure against-the-spread (ATS) grading helpers.

No database or framework imports: these functions are shared by the
predictions API and can be reused by batch jobs or a future RAG pipeline.
"""

from __future__ import annotations

from enum import Enum

# Sheets and data feeds disagree on a few abbreviations; normalize to one set.
TEAM_ALIASES: dict[str, str] = {
    "WAS": "WSH",
    "JAC": "JAX",
    "LA": "LAR",
}

PUSH = "PUSH"


class PickResult(str, Enum):
    WIN = "W"
    LOSS = "L"
    PUSH = "P"


def normalize_team(team: str) -> str:
    """Upper-case, trim, and map known aliases ("det" -> "DET", "was" -> "WSH")."""

    code = team.strip().upper()
    if not code:
        raise ValueError("team abbreviation is empty")
    return TEAM_ALIASES.get(code, code)


def parse_matchup(matchup: str) -> tuple[str, str]:
    """Split "AWAY@HOME" into normalized (away, home) abbreviations."""

    parts = matchup.split("@")
    if len(parts) != 2:
        raise ValueError(f"matchup must look like 'AWAY@HOME', got {matchup!r}")
    away, home = (normalize_team(p) for p in parts)
    if away == home:
        raise ValueError(f"matchup has the same team on both sides: {matchup!r}")
    return away, home


def canonical_matchup(matchup: str) -> str:
    away, home = parse_matchup(matchup)
    return f"{away}@{home}"


def team_in_matchup(team: str, away: str, home: str) -> str:
    """Normalize ``team`` and ensure it is one of the two sides; return it."""

    code = normalize_team(team)
    if code not in (away, home):
        raise ValueError(f"{team!r} is not in matchup {away}@{home}")
    return code


def ats_winner(
    *,
    away_team: str,
    home_team: str,
    favorite: str,
    spread: float,
    away_score: int,
    home_score: int,
) -> str:
    """Return the team that covered, or ``PUSH``.

    favorite margin > |spread| -> favorite covers; < -> underdog covers;
    == -> push (only possible on whole-number lines).
    """

    away, home = normalize_team(away_team), normalize_team(home_team)
    fav = team_in_matchup(favorite, away, home)
    dog = home if fav == away else away
    fav_score, dog_score = (away_score, home_score) if fav == away else (home_score, away_score)
    margin = fav_score - dog_score
    line = abs(spread)
    if margin > line:
        return fav
    if margin < line:
        return dog
    return PUSH


def grade_pick(pick: str, winner: str) -> PickResult:
    """Grade a normalized pick against an ``ats_winner`` result."""

    if winner == PUSH:
        return PickResult.PUSH
    return PickResult.WIN if normalize_team(pick) == winner else PickResult.LOSS
