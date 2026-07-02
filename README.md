# AI Intelligence Agent

A personal AI Intelligence Agent that automatically collects AI, Data Engineering,
Machine Learning, and software engineering news every week, filters noise, and
generates a personalized newsletter.

> **"What happened this week that will make me a better AI/Data Engineer?"**

---

## Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Foundation: config, database, models, logging | ✅ Done |
| 2 | Collectors: RSS, arXiv, GitHub Trending | ✅ Done |
| 3 | Processing: cleaning, deduplication, ranking | 🔜 Next |
| 4 | LLM summarization (Gemini 2.5 Flash) | 🔜 Planned |
| 5 | Newsletter generation (Markdown → HTML → PDF) | 🔜 Planned |
| 6 | GitHub Actions automation | ✅ Workflow written |

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/yourname/ai-news-agent.git
cd ai-news-agent

# Install uv if you haven't already
curl -Ls https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync
```

### 2. Configure

```bash
# Copy the example env file
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY (only needed for Phase 4+ LLM summarization)
```

Edit `config/config.yaml` to add/remove RSS feeds, arXiv categories, or GitHub languages.

### 3. Run the collection pipeline

```bash
uv run main.py
```

This will:
- Fetch articles from configured RSS feeds (OpenAI, Anthropic, Google DeepMind, Hugging Face, NVIDIA)
- Fetch recent papers from arXiv (cs.CL, cs.AI, cs.LG, cs.MA)
- Scrape GitHub Trending (Python, Jupyter Notebook)
- Store everything in `data/news.db` (SQLite)

### 4. Run the tests

```bash
uv run python -m pytest tests/ -v
```

All tests run fully offline (no API keys, no network calls).

---

## Project Structure

```
ai-news-agent/
│
├── config/
│   ├── config.yaml          # All source/schedule/LLM configuration
│   └── loader.py            # Typed config loader
│
├── collector/
│   ├── base.py              # BaseCollector interface
│   ├── rss.py               # RSS 2.0 + Atom (company blogs)
│   ├── arxiv.py             # arXiv API (research papers)
│   └── github.py            # GitHub Trending (HTML scraper)
│
├── database/
│   ├── database.py          # SQLite init, connection management
│   └── models.py            # Article model + upsert logic
│
├── scheduler/
│   ├── weekly.py            # Collection orchestrator
│   └── logging_setup.py     # Shared logger configuration
│
├── processing/              # Phase 3 — coming soon
├── llm/                     # Phase 4 — coming soon
├── newsletter/              # Phase 5 — coming soon
│
├── tests/
│   └── test_phase1_2.py     # 33 tests, all offline
│
├── .github/workflows/
│   └── weekly.yml           # Runs every Monday 08:00 UTC
│
├── .env.example             # Copy to .env and add your keys
├── main.py                  # Entry point
└── pyproject.toml           # uv / Python project config
```

---

## GitHub Actions Setup

1. Push this repo to GitHub.
2. Go to **Settings → Secrets and variables → Actions**.
3. Add `GEMINI_API_KEY` as a repository secret (needed for Phase 4+, safe to add now).
4. The workflow at `.github/workflows/weekly.yml` runs automatically every Monday at 08:00 UTC.
5. You can also trigger it manually from the **Actions** tab → **Weekly AI News Collection** → **Run workflow**.

The workflow commits `data/news.db` and the `output/` folder back to the repo after each run,
so your collected data persists across runs without any external storage.

---

## Adding More RSS Feeds

Edit `config/config.yaml`:

```yaml
sources:
  rss:
    feeds:
      - name: "Meta AI"
        url: "https://ai.meta.com/blog/feed/"
        category: "company"
```

No code changes needed — the collector picks up new feeds automatically.

---

## What's Next (Phase 3)

- `processing/cleaner.py` — strip HTML, remove boilerplate from article content
- `processing/deduplicate.py` — detect near-duplicate stories across sources using title similarity
- `processing/ranking.py` — score articles by source quality, freshness, GitHub stars, etc.
- `processing/classifier.py` — auto-assign topic categories (LLM, Data Engineering, Agents, etc.)
