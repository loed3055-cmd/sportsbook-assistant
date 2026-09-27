# Launch team

Five Claude Code subagents, defined in `.claude/agents/`. The **main session is
the lead**. It breaks tasks down, delegates, merges results, and talks to Edwin.
Subagents can't spawn subagents, so the lead is the only coordinator. This is
the orchestrator-workers pattern [1].

| Agent | Owns | Edits code? |
|---|---|---|
| `backend-engineer` | FastAPI, models, Alembic, predictions/grading | Yes |
| `data-engineer` | Connectors, ETL normalization, pick'em generator, Sheets import | Yes |
| `platform-engineer` | Test harness, CI, schedulers, hosting, Terraform | Yes; prepares deploys, never applies them |
| `qa-reviewer` | Independent test runs and diff review | No |
| `security-compliance` | Secrets, ASVS/SSDF, dependencies, data ToS, gambling copy | No |

## Why this shape
- **Five roles, not more.** Multi-agent systems use about 15× the tokens of a
  chat [2]. Product, docs, and priority calls stay with the lead and Edwin.
- **One verifier, one report format.** The three failure categories found in
  multi-agent systems are system design issues, inter-agent misalignment, and
  task verification [5]. So every engineer returns the same handoff report,
  and `qa-reviewer` independently checks work before it counts as done.
  Role-specific procedures with structured outputs follow MetaGPT [3] and
  ChatDev [4].
- **Short specs (≤ 50 lines).** Models use information in the middle of long
  contexts less reliably [6].

## Delegation flow
1. Lead writes the task: intent, scope, done-criteria.
2. Engineer implements it and returns a handoff report.
3. `qa-reviewer` returns PASS/FAIL. On FAIL, it goes back to step 2.
4. If the change is user-facing, deployable, or adds credentials, data sources,
   or dependencies, `security-compliance` reviews it.
5. Lead summarizes for Edwin. **Edwin approves** any deploy, `terraform apply`,
   scheduler install, push, or PR.

## Launch backlog
Pick'em goes first, then the backend platform.

- **M0: Test harness.** *(Done 2026-09-26.)* Fix `make test` (Dockerfile.test copies the wrong
  files) and add the missing `pytest-asyncio` and `python-multipart`.
  Reconcile `psycopg2` vs `psycopg[binary]`. Make CI green. *platform → qa.*
- **M1: Pick'em live.** *(Built and reviewed; waiting on Edwin's setup in docs/pickem-setup.md.)* Today the weekly CSV comes from
  `scripts/make_pickem_sheet.py` and Edwin imports it into the pool's Google
  Sheet by hand. The goal is to automate the import.
  - *data-engineer:* add a Sheets writer that uses the Google Sheets API. It
    stays a separate module so the generator keeps working on stdlib only.
    First confirm with Edwin how an import lands in the sheet: a new tab per
    week, or appended rows.
  - *platform-engineer:* propose a scheduler and wait for Edwin to pick one.
    Options: macOS launchd/cron (runs only while the Mac is awake), or a
    GitHub Actions `schedule` workflow (always on, credentials held in repo
    secrets). Set an SLO for when the sheet must land [10].
    Chosen: the GitHub Actions cron (Wed 13:00 UTC, keyless WIF). One-time setup is in `docs/pickem-setup.md`.
  - The job is deterministic, so no LLM agent is needed. Start simple [1].
    Keep a Gemini/LangGraph agent in reserve for a step that needs judgment,
    such as reading messy pick cells.
  - *security-compliance:* review how Google credentials are stored before
    anything runs unattended.
- **M2: Real ingest.** Replace the mock `fetch_odds_from_api` with The Odds
  API. Add retries, respect rate limits, and write `jobs` rows. *data → qa → security.*
- **M3: Cloud backend.** Multi-stage Dockerfile, a CI deploy job, and a
  Terraform plan for Postgres plus the API. *platform → qa → security.* Edwin runs `apply`.

## References
Checked 2026-09-24. Each `[n]` in an agent spec points here.

1. Anthropic, "Building Effective AI Agents," Dec 2024. Covers "start simple"
   and orchestrator-workers. https://www.anthropic.com/engineering/building-effective-agents
2. Anthropic, "How we built our multi-agent research system," Jun 2025.
   "Multi-agent systems use about 15× more tokens than chats."
   https://www.anthropic.com/engineering/multi-agent-research-system
3. Hong et al., "MetaGPT: Meta Programming for a Multi-Agent Collaborative
   Framework," ICLR 2024 (oral). Encodes SOPs into prompt sequences and has
   agents check intermediate outputs. https://arxiv.org/abs/2308.00352
4. Qian et al., "ChatDev: Communicative Agents for Software Development,"
   ACL 2024. https://arxiv.org/abs/2307.07924
5. Cemri et al., "Why Do Multi-Agent LLM Systems Fail?", 2025. Identifies 14
   failure modes in three categories: system design, inter-agent misalignment,
   task verification. https://arxiv.org/abs/2503.13657
6. Liu et al., "Lost in the Middle: How Language Models Use Long Contexts,"
   TACL 2024. https://arxiv.org/abs/2307.03172
7. Nagappan, Maximilien, Bhat, Williams, "Realizing quality improvement
   through test driven development," *Empirical Software Engineering* 13(3),
   2008. Pre-release defect density fell 40–90% across three Microsoft teams
   and one IBM team; initial development time rose 15–35%.
   https://doi.org/10.1007/s10664-008-9062-z
8. Sculley et al., "Hidden Technical Debt in Machine Learning Systems,"
   NeurIPS 2015. Names undeclared consumers and data dependencies as risks.
   https://papers.nips.cc/paper/5656-hidden-technical-debt-in-machine-learning-systems
9. DORA, "DORA's software delivery performance metrics." Five metrics: change
   lead time, deployment frequency, failed deployment recovery time, change
   fail rate, deployment rework rate. https://dora.dev/guides/dora-metrics-four-keys/
10. Beyer et al., *Site Reliability Engineering*, Google, 2016, ch. 4
    "Service Level Objectives." https://sre.google/sre-book/service-level-objectives/
11. Sadowski, Söderberg, Church, Sipko, Bacchelli, "Modern Code Review: A Case
    Study at Google," ICSE-SEIP 2018. Most changes are small and have one
    reviewer. https://doi.org/10.1145/3183519.3183525
12. NIST SP 800-218, "Secure Software Development Framework (SSDF) Version
    1.1," Feb 2022. https://csrc.nist.gov/pubs/sp/800/218/final
13. OWASP Application Security Verification Standard 5.0.0, May 2025, with
    levels L1–L3. https://github.com/OWASP/ASVS
