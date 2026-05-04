# exa-investor — Autonomous Financial AI Agent

> **Distributed Inference System for Swing Trade Decision-Making**  
> Multi-agent consensus architecture powered by Multi-HyDE RAG, ChromaDB, and a 3-tier distributed compute infrastructure.

---

## Abstract

**exa-investor** is a research-oriented autonomous financial AI agent designed for information science study.  
The system automatically collects S&P 500 financial corpora, vectorizes them into a local knowledge base, and runs a pipeline of five specialized agents — each with strictly scoped permissions — to produce compliant swing-trade decisions with full reasoning logs.

The project's long-term objective is to build a self-improving RAG pipeline that accumulates agent reasoning traces and feeds them back into future inference cycles via a local LLM (Llama 3.1 on Ollama), enabling continuous knowledge distillation without external API dependency.

---

## System Architecture

The system operates across three physically distinct compute tiers, coordinated by a shared Git repository and scheduled cron jobs with JST/UTC alignment.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        TIER 1 — Cloud Server                           │
│                        (www.dmgpt.site / Linux)                        │
│                                                                         │
│   cron 07:30 JST    →   run_pipeline.py --hybrid                       │
│   cron 23:00 JST    →   auto_push.sh  (git commit + push to GitHub)    │
│                                                                         │
│   • S&P 500 daily screening (yfinance + Gemini 2.5 Flash)              │
│   • 5-Agent consensus pipeline  →  BBS shared memory                   │
│   • Financial corpus auto-collection  (build_corpus.py, 503 tickers)   │
│   • Training data accumulation  (data/training/training_data.jsonl)    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │  GitHub (auto-push)
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     TIER 2 — Edge Controller                           │
│                     (ThinkPad E16 / RAM 32 GB)                         │
│                                                                         │
│   • Librarian.py  — task orchestration & cloud log sync               │
│   • ChromaDB  — persistent vector store (financial_corpus collection)  │
│   • RAG search host  (rag_test.py / rag_search skill)                  │
│   • Streamlit dashboard  (dashboard.py)                                │
│   • GitHub auto-push with JST-aware cron scheduling                    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │  Inference requests
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    TIER 3 — GPU Inference Node                         │
│                    (ASRock RX 5700 XT / 8 GB VRAM)                    │
│                                                                         │
│   • RTC-scheduled wake (early morning auto power-on via motherboard)   │
│   • Ollama + Llama 3.1  — offline inference & reasoning-log analysis  │
│   • Knowledge distillation  →  Obsidian Markdown vault (auto-export)  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Agent Architecture

Five specialized agents communicate exclusively through a shared **BBS (Bulletin Board System)** text memory. No agent holds direct references to another; each reads and writes to the BBS in a defined phase order.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Phase 1 — Parallel Information Collection                           │
│                                                                      │
│   NewsAgent            FundamentalAgent        TechnicalAgent        │
│   [news_monitor]       [rag_search]            [technical_calc]      │
│   RSS + LLM sentiment  Multi-HyDE × ChromaDB   RSI / MACD / MA25    │
│        │                      │                       │              │
│        └──────────────────────┴───────────────────────┘             │
│                               │                                      │
│                         BBS (shared memory)                          │
│                               │                                      │
│  Phase 2 — Integrated Judgement                                      │
│                               │                                      │
│                         ManagerAgent                                 │
│                 [News 30% / FA 40% / TA 30%]                         │
│                               │                                      │
│  Phase 3 — Compliance Gate                                           │
│                               │                                      │
│                       ComplianceAgent                                │
│               [8 rules enforced, REJECT / MODIFY / PASS]            │
│                               │                                      │
│                    LINE push notification                            │
└──────────────────────────────────────────────────────────────────────┘
```

### Agent Permission Matrix

| Agent | Role | Permitted Skills |
|---|---|---|
| **NewsAgent** | Fetches Google News RSS, runs LLM sentiment classification (positive / neutral / negative) | `news_monitor` |
| **FundamentalAgent** | Searches the financial corpus via Multi-HyDE; evaluates balance-sheet quality and growth | `rag_search` |
| **TechnicalAgent** | Fetches daily OHLCV via yfinance; computes RSI, MACD, MA25, volume spike score | `technical_calc` |
| **ManagerAgent** | Reads BBS, produces weighted-consensus BUY/HOLD/SELL action with confidence score | _(BBS read-only)_ |
| **ComplianceAgent** | Applies 8 hard compliance rules; REJECT or MODIFY non-conforming decisions | _(BBS read-only)_ |
| **ExitAgent** | Monitors open positions; triggers stop-loss or take-profit exits | `portfolio_monitor` |

### Multi-HyDE

**Hypothetical Document Embeddings (HyDE)** extended to multi-hypothesis generation.  
For each query, the system instructs the LLM to produce three hypothetical excerpts as they would appear in a real earnings document, then embeds all three alongside the original query. This significantly raises recall for domain-specific financial terminology.

---

## Features

### Financial Knowledge Base (`build_corpus.py`)
- Fetches ticker universe from Wikipedia S&P 500 list (503 tickers)
- Retrieves `sector`, `industry`, and `longBusinessSummary` via yfinance for each ticker
- Saves one JSON per ticker under `data/knowledge_base/` with explicit **metadata / content separation** — ready for ChromaDB metadata filtering
- **Resume functionality**: skips already-downloaded tickers on re-run; safe to interrupt at any time
- **IP-ban mitigation**: randomized `sleep(1–3 s)` between requests with `tqdm` progress bar

```json
{
  "metadata": { "ticker": "NVDA", "name": "NVIDIA Corporation",
                "sector": "Technology", "industry": "Semiconductors", ... },
  "content":  { "long_business_summary": "NVIDIA Corporation operates as a data center ..." }
}
```

### RAG Prototype (`rag_test.py`)
- Initializes a `PersistentClient` ChromaDB at `data/chroma_db/`
- Embeds all `long_business_summary` fields via `all-MiniLM-L6-v2` (default Chroma EF)
- Supports **semantic search** and **metadata-filtered hybrid queries**
- Batch upsert with resume (existing document IDs are skipped)

### Streamlit Dashboard (`dashboard.py`)
- Real-time visualization of agent reasoning process per ticker
- BBS session log browser with per-agent colour-coded badges
- Confidence score gauges, compliance decision timeline, and open-position monitor

### Automated Git Pipeline (`auto_push.sh`)
- Detects uncommitted changes via `git status -s`; skips cleanly if none
- Commits with timestamped message, appends entry to `README.md` Development History
- `git push` exit-code checked; logs `ERROR` on failure rather than false-positive success
- Scheduled at **23:00 JST** (`0 14 * * 1-5` UTC) via cron

---

## Compliance Rules

`ComplianceAgent` enforces the following eight rules mechanically. Any violation triggers `REJECT` or `MODIFY` before a decision reaches the execution layer.

| Rule ID | Constraint |
|---|---|
| RULE-01 | Maximum **20%** of total assets per ticker (concentration limit) |
| RULE-02 | **No consecutive BUY within 3 trading days** on the same ticker (anti-averaging) |
| RULE-03 | Stop-loss must be set within **−8%** of entry price (hard cap) |
| RULE-04 | Recommended hold period **≤ 20 trading days** |
| RULE-05 | Confidence score **< 50 → forced HOLD** |
| RULE-06 | **BUY prohibited** when negative news detected for the ticker |
| RULE-07 | Maximum **4 concurrent positions**, total exposure **≤ 60%** of assets |
| RULE-08 | **Speculation without evidence is prohibited** as a decision basis |

---

## Directory Structure

```
exa-investor/
├── agents/                      # Agent implementations
│   └── fundamental_agent.py
├── skills/                      # Skill modules (isolated, side-effect-free)
│   ├── news_monitor.py          # RSS fetch + LLM sentiment
│   ├── rag_search.py            # Multi-HyDE × ChromaDB search
│   ├── technical_calc.py        # RSI / MACD / MA25
│   ├── portfolio_monitor.py     # Alpaca position health check
│   └── training_data_collector.py
├── rules/
│   └── swing_trade_rules.md     # 8 compliance rules (machine-readable)
├── data/
│   ├── knowledge_base/          # Per-ticker JSON corpus (S&P 500, 503 tickers)
│   ├── chroma_db/               # ChromaDB persistent vector store
│   └── training/
│       └── training_data.jsonl  # Agent reasoning trace accumulation
├── bbs/                         # BBS session logs (agent shared memory)
│   └── YYYYMMDD_HHMMSS.json
├── main.py                      # Full pipeline orchestrator
├── run_pipeline.py              # Cron-triggered hybrid screening pipeline
├── build_corpus.py              # S&P 500 financial corpus builder
├── rag_test.py                  # ChromaDB RAG search prototype
├── dashboard.py                 # Streamlit visualization dashboard
├── auto_push.sh                 # Automated Git commit + push (JST-aware)
└── requirements.txt
```

---

## Tech Stack

| Category | Technology |
|---|---|
| LLM (cloud) | Google Gemini 2.5 Flash (`langchain-google-genai`) |
| LLM (local) | Llama 3.1 via Ollama (GPU node) |
| Embeddings | `all-MiniLM-L6-v2` (ChromaDB default) / `intfloat/multilingual-e5-small` |
| Vector DB | ChromaDB (PersistentClient) |
| RAG method | Multi-HyDE (multi-hypothesis hypothetical document embeddings) |
| Financial data | yfinance (daily OHLCV + fundamentals) |
| News | Google News RSS (`feedparser`) |
| Trade execution | Alpaca Markets API (`alpaca-py`) |
| Notification | LINE Messaging API |
| Dashboard | Streamlit + Plotly |
| Knowledge vault | Obsidian Markdown (GPU node auto-export) |
| Scheduling | cron (UTC-aware, JST-aligned) |

---

## Setup

### 1. Clone & install

```bash
git clone https://github.com/sarada1001/ai-investor-bot.git
cd exa-investor
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Environment variables

```bash
cp .env.example .env   # then fill in your keys
```

```env
GOOGLE_API_KEY=          # Google AI Studio (Gemini 2.5 Flash)
LINE_ACCESS_TOKEN=       # LINE Messaging API
LINE_USER_ID=
ALPACA_API_KEY=          # Alpaca Markets (paper or live)
ALPACA_SECRET_KEY=
```

### 3. Build the financial corpus

```bash
# Test run (5 tickers)
python build_corpus.py          # TEST_MODE = True (default)

# Full run — S&P 500 (≈ 20 min, resume-safe)
# Set TEST_MODE = False in build_corpus.py, then:
python build_corpus.py
```

### 4. Ingest into ChromaDB & verify search

```bash
python rag_test.py
```

### 5. Run the agent pipeline

```bash
python run_pipeline.py --hybrid   # screening + agent pipeline
streamlit run dashboard.py        # visualisation
```

---

## Roadmap

- [ ] **Semantic chunking** — replace whole-document embedding with paragraph-level chunks for finer retrieval granularity
- [ ] **Multi-HyDE upgrade** — apply the existing Multi-HyDE technique to the S&P 500 corpus (currently used only for IR PDF search)
- [ ] **Self-Refine / Reflexion** — inter-agent debate and self-critique loops to improve reasoning quality before the compliance gate
- [ ] **Reasoning-log feedback loop** — distil daily agent traces (via Llama 3.1 on GPU node) into the ChromaDB knowledge base for continual self-improvement
- [ ] **FinanceBench evaluation** — systematic benchmarking of RAG retrieval quality against the FinanceBench QA dataset

---

## 🚀 Latest Daily Pick

> 最終更新: 2026-05-02 10:42

### 本日の精鋭3銘柄 (2026-05-02)

| # | ティッカー | スコア | 価格 | 選出理由 |
|---|-----------|--------|------|---------|
| 1 | **NVDA** | 0.7123 | $875.50 | RSI=28.3（売られすぎ） / 出来高2.4倍スパイク / MA20から4.2%下方乖離 |
| 2 | **AAPL** | 0.5210 | $182.30 | RSI=72.1（強トレンド） / 出来高1.8倍スパイク / MA20から3.1%上方乖離 |
| 3 | **MSFT** | 0.4800 | $415.20 | MA20から2.5%上方乖離 |

#### 指標内訳

| ティッカー | RSI | VOL倍率 | VOLスコア | MAスコア | 総合スコア |
|-----------|-----|--------|----------|--------|----------|
| NVDA | 28.3 | 2.40x | 0.700 | 0.840 | 0.7123 |
| AAPL | 72.1 | 1.80x | 0.400 | 0.620 | 0.5210 |
| MSFT | 55.0 | 1.20x | 0.100 | 0.500 | 0.4800 |

---

## 🔄 Development History
- 📅 **2026-05-04 09:27:46** | 🛠️ **内容:** `auto-backup: 2026-05-04 09:27:46 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/13ff9292a4ae19fdd8ec8522ac84e7c8902604c0)
- 📅 **2026-05-01 23:00:01** | 🛠️ **内容:** `auto-backup: 2026-05-01 23:00:01 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/9e699096cffdb4900efe87a14fc8448dc2ca2c32)
- 📅 **2026-05-01 14:28:10** | 🛠️ **内容:** `auto-backup: 2026-05-01 14:28:10 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/fadd468aca11b8174e413a31cff29f39e610f41e)
