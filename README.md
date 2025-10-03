# Sportsbook Assistant (MVP)

A FastAPI-based backend to aggregate sportsbook odds, support player props, and serve structured data for querying, analytics, or an agentic layer later.

---

## 🛠 Project Structure & Technology Stack
```psql
sportsbook-assistant/
├── .devcontainer/
│   └── devcontainer.json
├── alembic/
│   └── versions/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── db.py
│   ├── models/
│   ├── routers/
│   ├── schemas/
│   ├── services/
│   └── utils/
├── tests/
├── .gitignore
├── alembic.ini
├── Containerfile
├── Makefile
├── pyproject.toml
├── uv.lock
└── README.md
```

### Key components

- **FastAPI** for the HTTP API.
- **SQLAlchemy / Postgres** for relational storage.
- **Alembic** for schema migrations (future).
- **uv / pyproject.toml / uv.lock** for dependency & environment management.
- **Podman + devcontainer** for consistent dev environments.
- **Makefile** for developer convenience (build, run, serve, clean).

---

## 🧪 Local Development Setup (Phase 1)

### 1. Build and run dev container

Ensure Podman is installed and running (including Podman Machine on macOS).

From project root:

```bash
make build
make run
```
This will:

- Build a container image from ```Containerfile```
- Start a container with your project directory bind-mounted into ```/workspace```

Inside the container, you can run:
```bash
make serve
```
This starts FastAPI (```uvicorn```) listening on port 8000.

Your API should then be accessible at:
```http://localhost:8000```

---

### 2. Database (Teams Table)

In this phase, we’re focusing on the ```teams``` table:

- A SQLAlchemy model is defined for ```Team```.
- FastAPI router under ```/teams``` supports:
    - ```POST /teams``` → add a team
    - ```GET /teams``` → list all teams

Inside ```app/main.py```, we call ```Base.metadata.create_all(bind=engine)``` for now to auto-create tables (later replaced with Alembic).

.env (ignored by Git) should contain:

```bash
DATABASE_URL=postgresql+psycopg2://sportsuser:sportspass@localhost:5432/sportsbook
```

This is picked up by ```app/config.py```.

## Entity Relationship Diagram

```mermaid
erDiagram
    TEAMS ||--o{ PLAYERS : has
    TEAMS ||--o{ GAMES : "home/away"
    GAMES ||--o{ ODDS : has
    GAMES ||--o{ BOX_SCORES : has
    PLAYERS ||--o{ BOX_SCORES : contributes
    SPORTSBOOKS ||--o{ ODDS : provides

    TEAMS {
        int id PK
        string sport
        string league
        string name
        string short_name
        string external_ref
    }

    PLAYERS {
        int id PK
        int team_id FK
        string name
        string position
        string external_ref
    }

    GAMES {
        int id PK
        string sport
        string league
        int home_team_id FK
        int away_team_id FK
        datetime start_time
        jsonb result
        string external_ref
    }

    ODDS {
        int id PK
        int game_id FK
        int sportsbook_id FK
        string market
        string outcome
        decimal price
        datetime last_updated
    }

    BOX_SCORES {
        int id PK
        int game_id FK
        int player_id FK
        string stat_type
        decimal value
    }

    SPORTSBOOKS {
        int id PK
        string name
        string api_ref
    }
```

### Data Flow (Sequence Diagram)
```mermaid
sequenceDiagram
    participant User
    participant FastAPI
    participant OddsAPI
    participant SportsRef
    participant Database

    User->>FastAPI: Request odds for NFL/European Soccer
    FastAPI->>OddsAPI: Fetch live odds (FanDuel, DraftKings, etc.)
    OddsAPI-->>FastAPI: Return odds JSON
    FastAPI->>Database: Insert/Update odds

    User->>FastAPI: Request game results
    FastAPI->>SportsRef: Scrape box scores & results
    SportsRef-->>FastAPI: Return stats (teams, players, outcomes)
    FastAPI->>Database: Insert results + box scores

    User->>FastAPI: Compare sportsbooks for props
    FastAPI->>Database: Query odds + results
    Database-->>FastAPI: Aggregated view
    FastAPI-->>User: JSON/Frontend data
```
