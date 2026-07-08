# AI Weekly Intelligence Report

> **An automated AI intelligence pipeline that answers one question every Monday morning:**
> *"What happened this week that will make me a better AI/Data Engineer?"*

**Live newsletter →** [arrofi07.github.io/ai-news-agent](https://arrofi07.github.io/ai-news-agent)
&nbsp;·&nbsp;
**Archive →** [arrofi07.github.io/ai-news-agent/archive.html](https://arrofi07.github.io/ai-news-agent/archive.html)

![Newsletter Preview](docs/newsletter-preview.png)

---

## Overview

This is not a news scraper. It is a multi-stage data pipeline that collects
from 7+ sources, filters noise through deduplication and importance ranking,
summarizes with Gemini 2.5 Flash, and delivers a formatted newsletter — fully
automated, every Monday, at zero cost.

```
Every Monday 08:00 UTC
        │
        ▼
┌───────────────────┐
│    COLLECTION     │  RSS · arXiv API · GitHub Trending scraper
│    ~141 articles  │  Age-filtered · Retry with backoff · Per-source failure isolation
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│    PROCESSING     │  HTML cleaning · Jaccard deduplication · Keyword classification
│    ~20 articles   │  Importance ranking 0–100 · URL normalization
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│  SUMMARIZATION    │  Gemini 2.5 Flash · Structured JSON output
│                   │  Per-article: summary · why it matters · career impact
└────────┬──────────┘
         │
         ├──────────────────────────────────┐
         ▼                                  ▼
┌───────────────────┐            ┌──────────────────────┐
│  GITHUB PAGES     │            │    EMAIL DELIVERY     │
│  Public URL       │            │    Resend API         │
│  Auto-updated     │            │    HTML + plain text  │
└───────────────────┘            └──────────────────────┘
```

---

## Tech Stack

| Layer | Technology | Reason |
|---|---|---|
| Language | Python 3.12 | Best AI/data ecosystem |
| Package manager | uv | Fast, modern, reproducible |
| Database | SQLite (stdlib) | Zero config, perfect for this volume |
| LLM | Gemini 2.5 Flash | Fast, cheap, strong summarization |
| Automation | GitHub Actions | Free, auditable, no server needed |
| Hosting | GitHub Pages | Free, automatic deploy on every run |
| Email | Resend API | Clean REST API, generous free tier |
| Testing | pytest + unittest.mock | 93 tests, fully offline |

---

## Pipeline Details

### Phase 1+2 — Collection

Three collectors run in sequence. Each is independently fault-tolerant — a single source going down does not affect the others.

| Collector | Source | Method |
|---|---|---|
| `collector/rss.py` | OpenAI, Anthropic, DeepMind, Hugging Face, NVIDIA | feedparser + stdlib XML fallback |
| `collector/arxiv.py` | cs.CL, cs.AI, cs.LG, cs.MA | arXiv public Atom API (no key required) |
| `collector/github.py` | Python + Jupyter repos | GitHub Trending HTML scraper |

Key design decisions:
- **Age filter** — RSS articles older than 7 days are skipped, preventing ingestion of full feed archives
- **URL-based deduplication** — same URL across multiple runs updates in place, never creates duplicate rows
- **Retry with backoff** — up to 3 attempts per source with exponential backoff
- **`collection_runs` table** — every run is recorded with found/new counts and status for observability

### Phase 3 — Processing

Four sequential steps on the raw collected data:

**Cleaner** (`processing/cleaner.py`)
Strips HTML tags, decodes entities, removes UTM tracking parameters, eliminates boilerplate phrases ("Read more", "Subscribe", "Click here"), normalizes Unicode punctuation.

**Deduplicator** (`processing/deduplicate.py`)
Groups near-identical stories using Jaccard similarity on title token sets. Union-Find algorithm for transitive grouping. Canonical article is chosen by source quality rank — primary sources (OpenAI, Anthropic) beat secondary coverage. Duplicates are marked `importance = -1` and excluded from downstream steps.

**Classifier** (`processing/classifier.py`)
Keyword-based topic assignment across 10 categories: `llm`, `agents`, `rag`, `data_engineering`, `mlops`, `open_source`, `research`, `safety`, `company`, `python`. Priority ordering resolves multi-category matches. Articles can hold multiple tags.

**Ranker** (`processing/ranking.py`)
Composite 0–100 importance score:

```
Score = source_quality (0–30)
      + freshness      (0–25, linear decay over 7 days)
      + content_signal (0–25, stars/keywords/abstract length)
      + source_type    (0–20, company announcement > research > trending)
```

### Phase 4 — LLM Summarization

Each selected article is sent to Gemini 2.5 Flash with a structured prompt requesting JSON output:

```json
{
  "summary": "2-3 sentence plain-English summary",
  "why_it_matters": "significance for AI/Data engineers",
  "career_impact": "high | medium | low",
  "category": "llm | agents | rag | ...",
  "tags": ["tag1", "tag2"],
  "estimated_read_minutes": 3
}
```

Summaries are persisted to `articles.summary` in SQLite so re-runs never re-summarize already processed articles — saves API cost.

**Fallback behavior:** if `GEMINI_API_KEY` is not set or the API is unavailable, rule-based summaries are generated from the first two sentences of the cleaned content. The pipeline always produces output.

### Phase 5 — Newsletter Generation

Two output formats built from the same data:

- **Markdown** (`output/newsletter_YYYY-WNN.md`) — source of truth, committed to the repo
- **HTML** (`output/newsletter_YYYY-WNN.html`) — inline CSS, XSS-safe, deployed to GitHub Pages

Career Takeaways section is dynamically generated from the category distribution of that week's articles — if agents content dominates, the takeaway focuses on agent frameworks, and so on.

---

## Project Structure

```
ai-news-agent/
│
├── config/
│   ├── config.yaml          # All sources, schedule, LLM settings
│   ├── loader.py            # Typed, dot-accessible config loader
│   └── prompts.py           # All LLM prompts — separated from business logic
│
├── collector/
│   ├── base.py              # BaseCollector interface
│   ├── rss.py               # RSS 2.0 + Atom (feedparser + stdlib fallback)
│   ├── arxiv.py             # arXiv public Atom API
│   └── github.py            # GitHub Trending HTML scraper
│
├── processing/
│   ├── pipeline.py          # Orchestrates clean → dedup → classify → rank
│   ├── cleaner.py           # HTML stripping, URL cleaning, boilerplate removal
│   ├── deduplicate.py       # Jaccard similarity + Union-Find grouping
│   ├── ranking.py           # 0–100 composite importance scoring
│   └── classifier.py        # Keyword-based topic classification
│
├── llm/
│   ├── gemini.py            # Gemini 2.5 Flash client (retry, rate limit, fallback)
│   └── summarize.py         # Article selection + summarization orchestrator
│
├── newsletter/
│   ├── markdown.py          # Markdown builder (LLM prose or template fallback)
│   ├── html.py              # Styled HTML (inline CSS, email-safe, XSS-safe)
│   └── email.py             # Resend API delivery
│
├── database/
│   ├── database.py          # SQLite init, db_session context manager
│   └── models.py            # Article dataclass, URL-based upsert
│
├── scheduler/
│   ├── weekly.py            # Collection orchestrator, per-source failure isolation
│   └── logging_setup.py     # Structured logger (module-tagged, stdout)
│
├── tests/
│   ├── test_phase1_2.py     # 33 tests: config, DB, collectors
│   ├── test_phase3.py       # 31 tests: cleaner, dedup, classifier, ranker
│   └── test_phase4_5.py     # 29 tests: Gemini client, summarizer, newsletter
│
├── .github/workflows/
│   └── weekly.yml           # Tests → collect → Pages deploy → email
│
├── data/
│   └── news.db              # SQLite (committed, persists state between Actions runs)
│
├── output/                  # Generated newsletters (committed each Monday)
├── main.py                  # Entry point
└── pyproject.toml           # uv project config + dependencies
```

---

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) — `curl -Ls https://astral.sh/uv/install.sh | sh`
- Gemini API key (free) — [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)

### Installation

```bash
git clone https://github.com/Arrofi07/ai-news-agent.git
cd ai-news-agent
uv sync
```

### Configuration

```bash
cp .env.example .env
# Set GEMINI_API_KEY in .env
```

All collection sources, LLM settings, and scheduling are configured in `config/config.yaml`.
No code changes needed for common customizations.

### Run

```bash
uv run main.py
```

Output is written to `output/newsletter_YYYY-WNN.md` and `output/newsletter_YYYY-WNN.html`.

The pipeline runs without a Gemini API key — it falls back to rule-based summaries and a template newsletter, so you can test the full flow before adding credentials.

### Test

```bash
uv run python -m pytest tests/ -v
```

93 tests. All offline — no API keys, no network calls, no external services required.

---

## GitHub Actions Setup

The workflow at `.github/workflows/weekly.yml` runs every Monday at 08:00 UTC.

**Jobs:**
1. **Run Tests** — all tests must pass before collection starts
2. **Collect & Generate** — full pipeline, Pages deploy, email delivery, commit outputs back to repo

**Required secrets** (repo → Settings → Secrets and variables → Actions):

| Secret | Description |
|---|---|
| `GEMINI_API_KEY` | Gemini 2.5 Flash API key |
| `RESEND_API_KEY` | Resend API key for email delivery |
| `RESEND_TO_EMAIL` | Recipient email address |

**Manual trigger:** Actions tab → Weekly AI News Collection → Run workflow

---

## Configuration Reference

```yaml
sources:
  rss:
    max_age_days: 7          # Skip articles older than N days
    feeds:
      - name: "OpenAI"
        url: "https://openai.com/news/rss.xml"
        category: "company"

  arxiv:
    categories:
      - "cs.CL"              # Add/remove arXiv categories here
      - "cs.AI"
    max_results_per_category: 20

  github_trending:
    languages: ["python", "jupyter-notebook"]
    since: "weekly"          # daily | weekly | monthly

newsletter:
  max_articles: 15

llm:
  model: "gemini-2.5-flash"
```

---

## Known Limitations

These are documented tradeoffs, not oversights:

| Limitation | Impact | Planned fix |
|---|---|---|
| Jaccard dedup is title-only | Stories with different titles but same content are not grouped | Embedding-based semantic dedup (v2) |
| Ranking weights are hand-tuned | No feedback loop to validate quality | Collect implicit feedback signals (v2) |
| No schema migration system | DB schema changes require manual intervention | Versioned migration files (v2) |
| Gmail strips some CSS | `linear-gradient` and `box-shadow` don't render in Gmail | Email-specific CSS template (v2) |

---

## Roadmap

| Version | Feature | Status |
|---|---|---|
| v1.0 | Core pipeline: collect → process → summarize → deliver | ✅ Complete |
| v2.0 | Reddit + HackerNews collectors | 🔜 Planned |
| v2.0 | Embedding-based semantic deduplication | 🔜 Planned |
| v2.0 | Importance ranking feedback loop | 🔜 Planned |
| v2.0 | Email-client tested HTML template | 🔜 Planned |
| v3.0 | Personalization — topic weighting by interest | 🔜 Planned |
| v3.0 | Per-article learning resources ("what to study next") | 🔜 Planned |
| v3.0 | Telegram delivery option | 🔜 Planned |
| v4.0 | Knowledge base — semantic search over all collected articles | 🔜 Planned |
| v5.0 | Auto-generated weekly voice podcast | 🔜 Planned |

---

## Design Decisions

**SQLite over PostgreSQL** — at <1,000 articles per week, SQLite is simpler to operate, version-control, and reason about. The DB file is committed to the repo so GitHub Actions retains state between runs without any external storage dependency.

**Raw `sqlite3` over SQLAlchemy** — the schema is simple and stable. An ORM adds abstraction cost without benefit at this scale. Swap in Phase 4+ if multi-user or cloud hosting is needed.

**Keyword classifier over ML model** — fast, zero-cost, fully debuggable, works offline. The LLM refines categories per-article in Phase 4 anyway. ML classification would add a model dependency for marginal accuracy gain on well-defined technical topics.

**Template fallback for newsletter** — the pipeline always produces output, even if Gemini is unavailable. Reliability is more important than perfection for an unattended Monday morning job.

**feedparser with stdlib fallback** — `feedparser` handles malformed XML, encoding edge cases, and RSS/Atom variants robustly. The stdlib XML fallback ensures the collector works even if the package isn't installed, which matters for testing in restricted environments.

---

## License

MIT