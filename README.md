# MNI Investment Intelligence
> A multi-agent AI system for automated Korean stock market analysis, sector scoring, and investment insight generation.

---

## Overview

MNI Investment Intelligence is a personal investment research platform built on a **multi-agent architecture**, designed to automate the full pipeline from raw market data collection to actionable investment insights.

The system integrates real-time Korean market APIs, AI-powered analysis, and automated publishing — running on a combination of cloud (GitHub Actions) and local (Mac launchd) schedulers with zero manual intervention.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  MNI Agent Orchestration             │
│                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │
│  │ Quant Agent │  │  News Agent │  │YouTube Agent│ │
│  │  (core)     │  │  (daily)    │  │  (daily)    │ │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘ │
│         │                │                │         │
│  ┌──────┴──────┐  ┌──────┴──────┐         │         │
│  │  DART Agent │  │  Analysis   │         │         │
│  │ (disclosure)│  │  Agent(WIP) │         │         │
│  └──────┬──────┘  └──────┬──────┘         │         │
│         └────────────────┴────────────────┘         │
│                          │                          │
│              ┌───────────┴───────────┐              │
│              │    Publish Agent      │              │
│              │ Google Sheets│Telegram│              │
│              └───────────────────────┘              │
└─────────────────────────────────────────────────────┘
```

---

## Agents

### 1. Quant Agent `agents/quant_agent/`
The core engine. Runs sector-level quantitative analysis across 9 sectors and 20+ sub-sectors of the Korean market (KOSPI + KOSDAQ).

**Pipeline:**
- Fetches sector stock lists via Naver Finance crawling
- Retrieves fundamentals (PER, PBR, ROE, EPS, market cap) via **KIS (Korea Investment & Securities) API**
- Fetches 20-day rolling investor flow data (foreign / institutional / retail net buy) via KIS API
- Computes **Z-scores** for cross-sectional normalization within each sub-sector
- Generates **composite scores** (`-PER_Z - PBR_Z + ROE_Z + OPM_Z - Debt_Z`)
- Classifies stocks into quadrants: 💎 Gem (low PBR + high ROE), 🚀 Growth, ⚠️ Watch, 🚨 Trap
- Runs sector thermometer vs. hardcoded PER/PBR benchmarks

**Key design decisions:**
- Token issued once per session, reused across all API calls (KIS rate limit: 1 token/min)
- Modular sector config — adding a new sector requires a single dictionary entry
- `RUN_ONLY_SECTORS` flag for partial execution during development

**Output tabs in Google Sheets:**
| Tab | Content |
|---|---|
| `요약_DATE` | Sector-level average metrics |
| `섹터명_DATE` | Full stock list with all metrics |
| `스코어링_DATE` | TOP10 undervalued stocks per sub-sector |
| `사분면_DATE` | Quadrant classification for all stocks |
| `온도계_DATE` | Sector thermometer vs. benchmark |

---

### 2. News Agent `agents/news_agent/`
Daily macro news briefing, automatically published to a Telegram channel every morning at 07:00 SGT.

- Collects news across 3 categories: Central Bank Policy, Geopolitics, Investment Events
- Summarizes via **Claude API** (claude-sonnet)
- Formats and publishes to Telegram broadcast channel

---

### 3. YouTube Agent `agents/youtube_agent/`
Summarizes Korean investment YouTube content daily.

- Monitors 8 investment channels (4 per bot)
- Extracts transcripts via `youtube-transcript-api`, falls back to Whisper for non-subtitled videos
- Tracks processed video IDs to prevent duplicate summaries
- Two separate bots: public channel (MN Investment) + private channel (personal)

---

### 4. DART Agent `agents/dart_agent/`
Fetches and analyzes Korean corporate disclosures via the **DART (Data Analysis, Retrieval and Transfer System) API**.

- Pulls recent filings for target companies
- Analyzes for: new contracts, clinical trial stages (bio sector), technology licensing
- AI-powered summarization via Gemini API

---

### 5. Analysis Agent `agents/analysis_agent/` *(In Development)*
Planned: end-to-end stock report generation for 💎 Gem and 🚀 Growth stocks.

- Auto-extracts top-scoring stocks from quadrant output
- Combines quant data + DART disclosures + news context
- Generates structured investment reports via Claude API
- Publishes detailed reports to Google Sheets + summary cards to Telegram

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Market Data | KIS API (Korea Investment & Securities) |
| Disclosure Data | DART Open API |
| Web Scraping | BeautifulSoup4, Requests |
| AI Analysis | Claude API (Anthropic), Gemini API (Google) |
| Storage | Google Sheets via gspread |
| Messaging | Telegram Bot API |
| Scheduling (cloud) | GitHub Actions |
| Scheduling (local) | Mac launchd |
| IDE | Cursor (AI-assisted development) |

---

## Automation Schedule

| Agent | Schedule | Platform |
|---|---|---|
| Macro News Bot | Daily 07:00 SGT | GitHub Actions |
| YouTube Bot (public) | Mon–Sat 08:40 SGT | Mac launchd |
| YouTube Bot (personal) | Daily 08:00 SGT | Mac launchd |
| Quant Agent | Weekly / on-demand | Manual |

---

## Project Structure

```
MNI_Finance_project/
├── agents/
│   ├── quant_agent/          # Sector quant scoring engine
│   ├── news_agent/           # Macro news briefing bot
│   ├── youtube_agent/        # YouTube summarization bots
│   │   └── data/             # Processed video ID tracking
│   ├── dart_agent/           # DART disclosure analyzer
│   ├── single_stock_agent/   # Individual stock deep-dive
│   └── analysis_agent/       # AI report generation (WIP)
├── shared/
│   └── google_key.json       # GCP service account key
├── logs/                     # Agent execution logs
├── plists/                   # Mac launchd config files
├── docs/                     # Documentation
├── .github/workflows/        # GitHub Actions
└── .env                      # API keys (not committed)
```

---

## Key Design Patterns

**1. Modular agent architecture**
Each agent is independently executable and loosely coupled. Shared utilities (token management, Google Sheets client) are centralized.

**2. API-first data sourcing**
Migrated from brittle web scraping (Naver Finance) to stable API sources (KIS API) for core financial data. Scraping retained only where no API alternative exists (sector stock lists, dividend yield).

**3. Cost-aware AI usage**
- Heavy data processing: no AI (pure computation)
- Summarization: Gemini Flash (low cost)
- High-quality analysis: Claude Sonnet (targeted usage)
- Planned: local LLM via Ollama (Mac Mini M4 Pro) for zero marginal cost

**4. Incremental automation**
Cloud (GitHub Actions) handles always-on tasks. Local (launchd) handles tasks requiring local resources (Whisper transcription). Designed to migrate fully to local server once Mac Mini is provisioned.

---

## Roadmap

- [x] Sector quant engine (Z-score, composite scoring, quadrant)
- [x] KIS API integration (fundamentals + 20-day investor flow)
- [x] Google Sheets automated publishing (5 tab types)
- [x] Telegram broadcast automation
- [ ] Analysis Agent — AI-generated stock reports
- [ ] Local LLM integration (Ollama on Mac Mini)
- [ ] Historical data accumulation → dynamic benchmark PER/PBR
- [ ] Sector thermometer automation (vs. rolling average)
- [ ] KIS API full migration (sector lists)

---

## Why This Project Is Relevant to AI Product Development

This project was built to solve a real personal problem — the manual effort required to monitor Korean equity markets across multiple dimensions simultaneously. It demonstrates:

- **Agentic AI design**: multiple specialized agents with defined responsibilities and interfaces
- **Tool-calling patterns**: agents that call external APIs, process results, and chain outputs
- **Evaluation pipelines**: Z-score normalization, composite scoring, quadrant classification as systematic signal evaluation
- **Vibe coding with agentic tools**: built iteratively using Cursor + Claude, with the developer (non-engineer background) directing AI to implement, debug, and refactor
- **Real-world deployment**: running in production daily, not a demo

---

*Built by Min | Singapore | 2026*
