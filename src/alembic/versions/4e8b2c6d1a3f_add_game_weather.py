"""add game_weather

Revision ID: 4e8b2c6d1a3f
Revises: 7c3e1a9b2d4f
Create Date: 2026-09-28

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4e8b2c6d1a3f"
down_revision: str | Sequence[str] | None = "7c3e1a9b2d4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_weather",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("week", sa.Integer(), nullable=False),
        sa.Column("matchup", sa.String(), nullable=False),
        sa.Column("kickoff_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("venue_name", sa.String(), nullable=False),
        sa.Column("city", sa.String(), nullable=False),
        sa.Column("roof", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("temp_f", sa.Float(), nullable=True),
        sa.Column("wind_mph", sa.Float(), nullable=True),
        sa.Column("gust_mph", sa.Float(), nullable=True),
        sa.Column("precip_prob", sa.Float(), nullable=True),
        sa.Column("short_forecast", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("attribution", sa.String(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("season", "week", "matchup", name="uq_game_weather_game"),
    )
    op.create_index(op.f("ix_game_weather_id"), "game_weather", ["id"], unique=False)
    op.create_index(op.f("ix_game_weather_season"), "game_weather", ["season"], unique=False)
    op.create_index(op.f("ix_game_weather_week"), "game_weather", ["week"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_game_weather_week"), table_name="game_weather")
    op.drop_index(op.f("ix_game_weather_season"), table_name="game_weather")
    op.drop_index(op.f("ix_game_weather_id"), table_name="game_weather")
    op.drop_table("game_weather")
