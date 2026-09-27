# Project Roadmap

The roadmap outlines the Sportsbook Assistant’s evolution from a local proof-of-concept into a production-grade data and AI platform.
Each phase builds toward the project’s long-term goal: **an infrastructure blueprint for data-driven sports intelligence**.

---

## 🧩 Phase 1 — Foundation (Local MVP)

**Goal:** Build a working local FastAPI + Postgres prototype to ingest, normalize, and serve sportsbook odds data.

**Deliverables**
- [x] Containerized FastAPI backend (`src/app`)
- [x] Local Postgres integration via Podman
- [x] Teams and games tables with Alembic migrations
- [x] Basic odds ingestion from The Odds API (manual or automated job)
- [x] Devcontainer + Makefile workflow for local development
- [x] `.env` and `.env.example` with documented environment variables
- [x] Initial commit and branching strategy (`v0.1/teams`)

**Focus**
- Developer velocity and environment reproducibility
- Secure handling of secrets
- Local testing and GitHub Actions CI planning

---

## ⚙️ Phase 2 — Data Ingestion & ETL Services

**Goal:** Enable automated and user-triggered ingestion of sportsbook data across multiple providers.

**Deliverables**
- [ ] `/ingest/upload` endpoint for CSV or JSON uploads
- [ ] `/ingest/scrape` endpoint to trigger odds API scrapes
- [ ] Background job orchestration (Celery or APScheduler)
- [ ] Structured logging and monitoring of ETL runs
- [ ] Job metadata tracking table (`jobs`)

**Focus**
- API abstraction for sportsbook connectors
- Data validation and normalization schema
- Logging, retries, and observability

---

## ☁️ Phase 3 — Cloud-Ready Architecture

**Goal:** Transition from local development to cloud infrastructure with CI/CD automation.

**Deliverables**
- [ ] Multi-stage Dockerfiles (dev/test/prod)
- [ ] GitHub Actions pipelines for build/test/deploy
- [ ] Terraform-managed cloud resources (e.g., AWS ECS + RDS)
- [ ] CI secrets and environment variable management
- [ ] Data retention policies and archival storage (S3 or GCS)

**Focus**
- Secure, reproducible deployments
- Cost-efficient data retention
- Testing in isolated environments

---

## 🧠 Phase 4 — Intelligence & RAG Integration

**Goal:** Add LLM-powered query interfaces and semantic insights on historical odds data.

**Deliverables**
- [ ] Vector database integration (pgvector or Qdrant)
- [ ] Embedding pipeline for game, team, and odds metadata
- [ ] Optional public-data connector research for NCAA basketball stats,
      recruiting signals, coaching changes, and Kendra/vector document export
- [ ] Query agent orchestration (e.g., `OddsAgent`, `InsightAgent`)
- [ ] Natural language question interface (FastAPI endpoint or chatbot)
- [ ] Predictive or trend analysis features (e.g., “underdog hit rate”)

**Focus**
- RAG pipeline integration
- Agent-based orchestration for analytics
- AI explainability and reproducibility

---

## 🚀 Phase 5 — Open Platform & Community

**Goal:** Evolve the Sportsbook Assistant into an open, extensible data platform.

**Deliverables**
- [ ] Plugin system for community-built sportsbook connectors
- [ ] REST + SDK client for programmatic access
- [ ] Contributor documentation and examples
- [ ] Open dataset exports and research APIs
- [ ] Governance and licensing review

**Focus**
- Ecosystem growth and contributor experience
- Transparency and open data principles
- Alignment with broader sports analytics communities

---

### 🔁 Versioning Strategy

| Phase | Git Branch | Tag Prefix | CI/CD Target |
|-------|-------------|-------------|---------------|
| Phase 1 | `v0.1/*` | `v0.1.x` | Local Podman |
| Phase 2 | `v0.2/*` | `v0.2.x` | Dev/Test CI |
| Phase 3 | `v0.3/*` | `v0.3.x` | Staging Cloud |
| Phase 4 | `v0.4/*` | `v0.4.x` | AI/RAG Pipeline |
| Phase 5 | `v1.0/*` | `v1.x.x` | Production Release |

---

### 🧭 Alignment with Vision

This roadmap directly supports the project’s guiding philosophy:

> *The Sportsbook Assistant is not just a betting tool—it’s an infrastructure blueprint for data-driven sports intelligence, connecting ETL, analytics, and LLM-based reasoning into one developer-friendly stack.*

---
