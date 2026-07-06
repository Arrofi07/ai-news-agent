# AI Intelligence Agent

A personal AI Intelligence Agent that automatically collects AI, Data Engineering,
Machine Learning, and software engineering news every week — filters noise, ranks
by importance, summarizes with Gemini 2.5 Flash, and generates a ready-to-read newsletter.

> **"What happened this week that will make me a better AI/Data Engineer?"**

Every Monday morning you get something like this in your repo:

```
# AI Weekly Intelligence Report
Week 28 - 2026

## 🔥 Top Stories
## 📄 Research Worth Reading
## 🛠️ New Tools & Libraries
## 📈 Trending on GitHub
## 🎯 Career Takeaways

Estimated reading time: 10 minutes
```

---

## Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Foundation: config, SQLite, models, logging | ✅ Done |
| 2 | Collectors: RSS, arXiv, GitHub Trending | ✅ Done |
| 3 | Processing: clean, deduplicate, classify, rank | ✅ Done |
| 4 | LLM summarization (Gemini 2.5 Flash) | ✅ Done |
| 5 | Newsletter generation (Markdown + HTML) | ✅ Done |
| 6 | GitHub Actions automation (weekly + manual) | ✅ Done |

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/yourname/ai-news-agent.git
cd ai-news-agent

# Install uv if you don't have it
curl -Ls https://astral.sh/uv/install.sh | sh

uv sync
```

### 2. Add your Gemini API key

```bash
cp .env.example .env
# Edit .env and set GEMINI_API_KEY
# Get a free key at: https://aistudio.google.com/app/apikey
```

> **No key?** The pipeline still runs — it uses rule-based fallback summaries
> instead of LLM summaries, and a template newsletter instead of LLM-written prose.

### 3. Run the full pipeline

```bash
uv run main.py
```

Takes about 2–3 minutes. Output lands in `output/`:

```
output/
├── newsletter_2026-W28.md    ← Markdown (source of truth)
└── newsletter_2026-W28.html  ← Styled HTML (open in browser)
```

### 4. Run the tests

```bash
uv run python -m pytest tests/ -v
```

93 tests, all fully offline — no API keys, no network calls needed.

---

## How It Works

```
Every Monday 08:00 UTC
         │
         ▼
  ┌─────────────────┐
  │   COLLECTION    │  RSS feeds · arXiv · GitHub Trending
  └────────┬────────┘
           │  ~141 raw articles
           ▼
  ┌─────────────────┐
  │   PROCESSING    │  Clean HTML · Deduplicate · Classify · Rank 0–100
  └────────┬────────┘
           │  ~20 quality articles
           ▼
  ┌─────────────────┐
  │ SUMMARIZATION   │  Gemini 2.5 Flash · per-article JSON summary
  └────────┬────────┘
           │
           ▼
  ┌─────────────────┐
  │   NEWSLETTER    │  Markdown + HTML · committed back to repo
  └─────────────────┘
```

### Sources collected

| Source | Type | What |
|--------|------|------|
| OpenAI, Anthropic, DeepMind, Hugging Face, NVIDIA | RSS | Company blog posts (last 7 days) |
| arXiv | API | cs.CL, cs.AI, cs.LG, cs.MA papers |
| GitHub Trending | Scraper | Python + Jupyter repos, weekly |

### Processing pipeline

- **Cleaner** — strips HTML tags, decodes entities, removes UTM params, kills boilerplate ("Read more", "Subscribe")
- **Deduplicator** — Jaccard similarity on title tokens; groups near-identical stories (e.g. "OpenAI releases X" from 5 outlets → kept as one with all sources listed)
- **Classifier** — keyword rules assign categories: `llm`, `agents`, `rag`, `data_engineering`, `mlops`, `research`, `safety`, `open_source`, `python`, `company`
- **Ranker** — 0–100 score from: source quality (OpenAI=30) + freshness decay + content signals (stars, keywords) + source type bonus

---

## Project Structure

```
ai-news-agent/
│
├── config/
│   ├── config.yaml          # All sources, schedule, LLM settings
│   ├── loader.py            # Typed config loader (dot-accessible)
│   └── prompts.py           # All LLM prompts in one place
│
├── collector/
│   ├── base.py              # BaseCollector interface
│   ├── rss.py               # RSS 2.0 + Atom (feedparser + stdlib fallback)
│   ├── arxiv.py             # arXiv public Atom API (no key needed)
│   └── github.py            # GitHub Trending HTML scraper
│
├── processing/
│   ├── pipeline.py          # Orchestrates clean → dedup → classify → rank
│   ├── cleaner.py           # HTML stripping, URL cleaning, boilerplate removal
│   ├── deduplicate.py       # Jaccard similarity + Union-Find grouping
│   ├── ranking.py           # 0–100 importance scoring
│   └── classifier.py        # Keyword-based topic classification
│
├── llm/
│   ├── gemini.py            # Gemini 2.5 Flash client (retry, rate limit, fallback)
│   └── summarize.py         # Article selection + summarization orchestrator
│
├── newsletter/
│   ├── markdown.py          # Markdown builder (LLM prose or template fallback)
│   └── html.py              # Styled HTML (inline CSS, email-safe, XSS-safe)
│
├── database/
│   ├── database.py          # SQLite init + db_session context manager
│   └── models.py            # Article dataclass + URL-based upsert/dedup
│
├── scheduler/
│   ├── weekly.py            # Collection run orchestrator
│   └── logging_setup.py     # Shared structured logger
│
├── tests/
│   ├── test_phase1_2.py     # 33 tests: config, DB, collectors, orchestrator
│   ├── test_phase3.py       # 31 tests: cleaner, dedup, classifier, ranker
│   └── test_phase4_5.py     # 29 tests: Gemini client, summarizer, newsletter
│
├── .github/workflows/
│   └── weekly.yml           # Tests → collect → commit → artifact upload
│
├── data/
│   └── news.db              # SQLite database (committed, persists across runs)
│
├── output/                  # Generated newsletters (committed each Monday)
│
├── .env.example             # Copy to .env, add GEMINI_API_KEY
├── main.py                  # Entry point — runs the full 4-phase pipeline
└── pyproject.toml           # uv project config + dependencies
```

---

## GitHub Actions

The workflow at `.github/workflows/weekly.yml` runs every Monday at 08:00 UTC.

**Jobs:**

1. **Run Tests** — all 93 tests must pass before collection starts
2. **Collect & Generate Newsletter** — full pipeline, then commits outputs back to the repo

**Setup:**

1. Go to your repo → **Settings → Secrets and variables → Actions**
2. Add `GEMINI_API_KEY` as a repository secret
3. Trigger your first run manually: **Actions → Weekly AI News Collection → Run workflow**

After a successful run you'll see:
- `data/news.db` updated (SQLite, ~200KB)
- `output/newsletter_YYYY-WNN.md` and `.html` committed
- A downloadable artifact in the Actions run (kept 90 days)

---

## Configuration

All settings live in `config/config.yaml`. No code changes needed for common customizations.

### Add an RSS feed

```yaml
sources:
  rss:
    feeds:
      - name: "Meta AI"
        url: "https://ai.meta.com/blog/feed/"
        category: "company"
```

### Change the article age window

```yaml
sources:
  rss:
    max_age_days: 14    # default: 7
```

### Change arXiv categories

```yaml
sources:
  arxiv:
    categories:
      - "cs.CL"   # Computation & Language
      - "cs.AI"   # Artificial Intelligence
      - "cs.LG"   # Machine Learning
      - "cs.MA"   # Multi-Agent Systems
      - "cs.CV"   # Computer Vision — add this
```

### Switch LLM provider

```yaml
llm:
  provider: "gemini"          # gemini | openai | anthropic (future)
  model: "gemini-2.5-flash"
```

---

## Roadmap

| Version | Feature |
|---------|---------|
| v2 | Reddit + HackerNews collectors |
| v2 | Personalization — weight topics by your interests |
| v3 | Learning assistant — suggested resources per article |
| v3 | Email / Telegram / Discord delivery |
| v4 | Knowledge base — chat with your collected articles |
| v5 | Auto-generated voice podcast |

---

## Design Decisions

**Why SQLite?** Zero config, single file, perfect for <1000 articles/week. Swap to PostgreSQL when you need multi-user or cloud hosting.

**Why no SQLAlchemy?** Raw `sqlite3` is simpler to read, debug, and has no dependencies. For an MVP this size, an ORM adds complexity without benefit.

**Why keyword classification instead of ML?** Fast, debuggable, zero cost, works offline. The LLM refines categories in Phase 4 anyway.

**Why commit the DB to git?** GitHub Actions has no persistent storage between runs. Committing `data/news.db` is the simplest solution that requires no external services (no S3, no Postgres, no Redis). The DB stays small — ~200KB for a full week.

**Why template fallback for the newsletter?** The pipeline always produces output, even if the Gemini API is down, over quota, or the key isn't set. Reliability > perfection.