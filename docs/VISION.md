# Project Vision

## Problem
Sports bettors and analysts often struggle to compare odds, player props, and line movements across multiple sportsbooks in real time. Each sportsbook—FanDuel, DraftKings, BetMGM, ESPNBet, Fanatics, Underdog, Bet365—uses different APIs, data formats, and latency windows, making manual comparison inefficient and error-prone.
Additionally, there’s no open, developer-friendly way to persist this data historically for trend analysis or model training, leaving value and arbitrage opportunities undiscovered.

## Target User
- **Casual and professional bettors** seeking to identify favorable odds and line discrepancies.
- **Data analysts and sports data engineers** who want structured, historical odds data for modeling and prediction.
- **Sysadmins / ETL users** managing automated batch jobs to ingest and normalize sportsbook data into a shared data lake or warehouse.
- **Developers and AI researchers** using the dataset to power RAG (Retrieval-Augmented Generation) systems and agentic workflows for sports analytics.

## Success Metric
- ✅ The system can successfully ingest and normalize odds data from **≥5 major sportsbooks daily** with <5% API error rate.
- ✅ A user can upload or trigger batch jobs (scraper/ETL) to populate the database without manual intervention.
- ✅ Data can be queried and visualized in a unified schema (games, teams, odds, markets, outcomes).
- ✅ Early adopters use the API or exported dataset to perform line comparisons or automated decision support.
- ✅ Foundation for RAG and LLM agents is established via vectorized metadata and historical odds context.

## Why Software?
Existing odds comparison tools are designed for consumer frontends and closed ecosystems—they don’t expose structured APIs, job orchestration, or extensibility for data engineers.
This project builds an **open, composable, cloud-ready platform** that:
- Provides clean FastAPI-based ingestion endpoints and automated batch scraping.
- Stores normalized, queryable odds data in Postgres (and later vector DBs like Qdrant or pgvector).
- Demonstrates best-practice **cloud infrastructure architecture (Podman, GitHub Actions, Terraform)** aligned with real-world multi-cloud deployments.
- Enables experimentation and reproducibility for sports data analytics and agentic pipelines.

## Risks & Technical Limitations

The Sportsbook Assistant operates in a complex, data-driven ecosystem that spans licensed APIs, real-time scraping, and long-term analytics.
This section documents key risks and the planned mitigations to ensure the system remains compliant, performant, and extensible.

### 1. Data Licensing & API Sustainability
**Risk:** Each sportsbook’s odds data may be proprietary or restricted by rate limits and terms of service.
**Mitigation:**
- Abstract the ingestion layer so connectors can be swapped or extended for compliant providers.
- Introduce a local caching layer to minimize outbound API calls and respect rate limits.
- Prioritize partnerships or licensed data sources (e.g., The Odds API, Sportradar) for sustainability.

### 2. Data Quality & Normalization Complexity
**Risk:** Odds data formats differ widely (decimal vs. American odds, “moneyline” vs. “head-to-head”) leading to potential data loss or misalignment.
**Mitigation:**
- Define a canonical schema early (e.g., `sportsbook`, `market_type`, `team_home`, `team_away`, `odds_decimal`, `timestamp`).
- Validate ingested data through Pydantic models, Marshmallow schemas, or database constraints.
- Implement reconciliation tests comparing multiple sources for the same event.

### 3. Scheduling, Latency & ETL Reliability
**Risk:** High-volume scraping and scheduling across time zones can result in missing, duplicated, or stale odds snapshots.
**Mitigation:**
- Introduce a lightweight task scheduler (Celery, Dramatiq, or APScheduler) to orchestrate fetches.
- Persist raw logs and delta changes to enable reproducible odds timelines.
- Use distributed scraping (proxy rotation, exponential backoff) to prevent blocking or throttling.

### 4. Storage, Cost & Historical Retention
**Risk:** The dataset grows rapidly with every new sport, market, and day of odds history.
**Mitigation:**
- Partition Postgres tables by sport or ingestion date for query efficiency.
- Archive older data to cloud object storage (e.g., S3 or GCS) using Iceberg or Parquet formats.
- Define retention rules early (e.g., 90 days in Postgres, indefinite archive in lakehouse).

### 5. Differentiation & End-User Value
**Risk:** Without a unique differentiator, this system risks overlapping with existing odds aggregators or APIs.
**Mitigation:**
- Prioritize developer experience (reproducible devcontainer setup, well-documented schema, open API design).
- Integrate LLM/RAG pipelines to support intelligent queries such as
  *“Which sportsbook historically overprices underdogs in EPL matches?”*
- Add explainability metrics and predictive analytics to highlight unique insights over basic odds comparison.

---

### Summary Table

| Area | Primary Risk | Impact if Ignored | Planned Mitigation |
|------|---------------|------------------|--------------------|
| **Licensing** | Noncompliant data use or API bans | Legal exposure, blocked endpoints | Use licensed APIs, caching, pluggable connectors |
| **Normalization** | Inconsistent odds and markets | Invalid comparisons, bad analytics | Canonical schema, data validation, reconciliation |
| **Scheduling** | Missed or stale data | Incomplete historical snapshots | Central scheduler, logging, backoff logic |
| **Storage** | Exponential data growth | Cost escalation, query latency | Table partitioning, cold storage, retention policies |
| **Differentiation** | Low adoption or redundancy | No strategic value | RAG integration, developer-first UX, analytics layer |

---

This approach formalizes your “known unknowns” and makes your architecture review–ready — perfect for inclusion in future design docs or a technical appendix.

## Future Considerations

The Sportsbook Assistant is designed not only as an MVP but as a **blueprint for a scalable, data-intensive AI system**.
As the project matures, its architecture should evolve to support production-grade ETL, cloud deployment, and agentic querying.
Below are key future directions aligned with the project’s philosophy of building a reusable infrastructure for sports intelligence.

### 1. Cloud-Native ETL & Microservices
- **Modularize ingestion**: Break scraping, normalization, and enrichment into microservices managed by a lightweight orchestrator (e.g., Prefect, Temporal, or AWS Step Functions).
- **Adopt event-driven design**: Use message queues (Kafka, RabbitMQ, or AWS SNS/SQS) to decouple data producers (scrapers) and consumers (normalizers or analytics jobs).
- **Introduce observability**: Add structured logging, metrics (Prometheus/Grafana), and alerting for failed jobs or missing data.
- **IaC alignment**: Manage all infrastructure with Terraform and Ansible to allow reproducible deployments across AWS, Azure, and GCP.

### 2. Data Lakehouse & Analytics Layer
- **Hybrid storage strategy**: Move raw and historical odds data to a data lake (S3 or GCS) using open table formats like Iceberg or Delta Lake.
- **Warehouse integration**: Mirror cleaned data into a query-optimized warehouse (e.g., BigQuery, Snowflake, or Redshift Spectrum).
- **Analytical models**: Build data marts for trend detection, line movement analytics, and bookmaker bias scoring.

### 3. Vector Database & LLM-Driven Insights
- **Semantic layer**: Store historical text and numeric metadata in a vector database (pgvector, Qdrant, or Pinecone).
- **RAG pipelines**: Enable retrieval-augmented generation queries such as
  _“Which sportsbook offers the best underdog value over the past 10 Premier League seasons?”_
- **Agent orchestration**: Develop a set of domain agents—`OddsAgent`, `TeamStatsAgent`, `InsightAgent`—that chain FastAPI endpoints, embeddings, and SQL queries to produce conversational analytics.

### 4. CI/CD and Automated Testing
- **Continuous integration**: Run containerized tests via GitHub Actions (build → test → push).
- **Security scanning**: Integrate Trivy or Bandit into pipelines to check image and code vulnerabilities.
- **Continuous delivery**: Automatically deploy tagged builds to a staging environment or ephemeral cloud sandbox.

### 5. User Interfaces & Access Control
- **Admin console**: Add a web dashboard for monitoring ingestion jobs, viewing logs, and triggering manual refreshes.
- **API key management**: Implement JWT or API key authentication for client access.
- **Role-based controls**: Differentiate between developers, analysts, and sysadmins (read/write/execute privileges).

### 6. Community & Extensibility
- **Open connectors**: Encourage the community to contribute new sportsbook APIs through a plugin system.
- **Documentation & SDKs**: Provide a Python SDK and REST client so others can interact with your normalized schema.
- **Research integrations**: Enable analysts to export datasets directly into notebooks or BI tools.

---

### Architectural Trajectory
The end state is a **modular, reproducible data platform**:
1. **ETL pipeline** continuously ingests sportsbook data.
2. **Lakehouse layer** preserves normalized historical datasets.
3. **Vector layer** powers natural-language sports intelligence via LLMs.
4. **Agentic orchestration** connects structured data and language mod


## Philosophy
Ultimately, the Sportsbook Assistant is not just a betting tool—it’s an **infrastructure blueprint for data-driven sports intelligence**, connecting ETL, analytics, and LLM-based reasoning into one developer-friendly stack.
