# ai-investor-bot — 自律型スイングトレードAIエージェント

> S&P500を対象にした、マルチエージェント合意方式の自律売買システム。  
> ステージゲート型パイプライン・Multi-HyDE RAG・Alpaca自動発注・Obsidian知識ベース連携。

---

## システム概要

5つの専門エージェントが共有メモリ（BBS）を通じて協調し、ManagerAgentが最終売買判断を行います。  
判断はCriticAgent（ローカルLLM）による独立審査を経てAlpacaへ自動発注されます。

```
┌──────────────────────────────────────────────────────────────────┐
│  Stage 1 — 安価スキャン（並列実行）                               │
│                                                                  │
│   TechnicalAgent   NewsAgent   MacroAgent   SocialAgent          │
│   RSI/MACD/SMA     ニュース     SPY/VIX      SNSセンチメント       │
│        └──────────────┴─────────────┴────────────┘              │
│                           BBS（共有メモリ）                       │
│                               │                                  │
│  Gate: Macro NEGATIVE → 即HOLD / Tech+News双方NEUTRAL → 即HOLD   │
│                               │                                  │
│  Stage 2 — ファンダメンタル分析（Gate通過時のみ）                  │
│                                                                  │
│              FundamentalAgent（Multi-HyDE RAG + EDGAR）          │
│                               │                                  │
│  Stage 3 — 最終判断                                               │
│                                                                  │
│    ManagerAgent → CriticAgent（Ollama） → 加重スコア算出          │
│                               │                                  │
│  Stage 4 — リスク計算（STRONG BUY時のみ）                         │
│                                                                  │
│        RiskAgent（Fixed Fractional + Kelly Criterion）           │
│                               │                                  │
│              Alpaca発注 / LINE通知 / Obsidianログ記録              │
└──────────────────────────────────────────────────────────────────┘
```

**STRONG BUY 判定条件（すべて満たすこと）**

- 加重スコア ≥ 0.60（FA×0.40 / Tech×0.20 / Macro×0.20 / News×0.10 / Social×0.10）
- Fundamental シグナル > 0（必須）
- Technical シグナル ≥ 0
- News シグナル ≥ 0
- Macro シグナル ≥ 0（NEGATIVE時は強制HOLD）

---

## エージェント一覧

| エージェント | 役割 | スキル |
|---|---|---|
| **TechnicalAgent** | RSI・MACD・SMA25乖離・出来高比を算出してLLM評価 | `technical_calc` |
| **NewsAgent** | Alpha Vantage / Finnhubからニュース取得 → センチメント判定 | `news_monitor` |
| **MacroAgent** | SPY・VIXでマクロ環境評価 | `macro_monitor` |
| **SocialAgent** | SNSセンチメント + hype_score（≥0.7で買い煽りペナルティ） | `social_monitor` |
| **FundamentalAgent** | Multi-HyDE RAG（Chroma）+ EDGAR自律取得で財務評価 | `rag_search`, `edgar_fetcher` |
| **ManagerAgent** | BBS集約・加重スコア算出・SNS買い煽り検出 | — |
| **CriticAgent** | Ollama（ローカルLLM）による独立審査・OVERRIDE可能 | `tools/critic_agent` |
| **RiskAgent** | ポジションサイジング・ストップロス算出 | `risk_calculator` |
| **ExitAgent** | 保有ポジション監視（+10%利確 / -5%損切 / THESIS_BROKEN） | `portfolio_monitor` |

---

## 実行方法

### 基本コマンド

```bash
# S&P500スクリーニング → 上位5銘柄をAI分析（発注あり）
python main.py --screen --notify-line

# ドライラン（発注なし・ログのみ）
python main.py --screen --dry-run

# 単一銘柄指定
python main.py --ticker AAPL --dry-run

# 複数銘柄ウォッチリスト
python main.py --tickers AAPL MSFT NVDA --dry-run

# スクリーニング結果の確認のみ（AI分析なし）
python main.py --screen --screen-only

# 24時間デーモン（自動スクリーニング + 自動売買）
python main.py --screen --daemon --notify-line
```

### 実行オプション一覧

| オプション | 説明 |
|---|---|
| `--screen` | S&P500スクリーニングで上位N銘柄を自動選出 |
| `--screen-only` | スクリーニング結果表示のみ（AI分析スキップ） |
| `--top-n N` | スクリーニング選出銘柄数（デフォルト: 5） |
| `--dry-run` | Alpaca発注をスキップ（テスト用） |
| `--hybrid` | 全ステージリアル分析 + 発注スキップ（本番前検証） |
| `--mock` | LLM/API呼び出しゼロ（システムフロー確認用） |
| `--notify-line` | 最終判断をLINE通知 |
| `--daemon` | 24時間自動取引ループ（市場閉場中は自動スリープ） |
| `--interval N` | デーモン評価間隔（秒、デフォルト: 3600） |
| `--exclude エージェント名` | 特定エージェントを除外（アブレーション実験用） |

### 知識ベース管理

```bash
# 日報生成（latest_summary.md に出力）
python server_librarian.py

# 取引ログをWikiに反映（Obsidian連携）
python server_librarian.py --ingest

# Wikiヘルスチェック（リンク切れ・孤児ページ・矛盾検出）
python scripts/lint_wiki.py
```

---

## S&P500スクリーナー

LLMを使わずテクニカルスコアで全503銘柄を高速スコアリングし、候補を絞り込みます。

| 指標 | 内容 |
|---|---|
| RSI | 過売買領域（30以下 or 70以上）を評価 |
| MACD | ゴールデンクロス / デッドクロス |
| SMA25乖離 | 移動平均からの乖離率 |
| 出来高比 | 直近20日平均との比較 |
| 52週高値からの下落率 | 押し目判定 |

当日キャッシュ機能あり（同日中は再スクリーニングをスキップ）。

---

## Multi-HyDE RAG

FundamentalAgentが採用する高精度検索手法。

クエリに対して「実際の決算書に書かれているであろう仮説的な回答」を3パターンLLMに生成させ、元のクエリと合わせてベクトル検索します。金融ドメイン固有の専門用語に対する再現率が大幅に向上します。

```
ユーザークエリ
  └→ LLMで仮説ドキュメント×3生成
       └→ [クエリ + 仮説×3] でChromaDB検索
            └→ 上位チャンクをコンテキストに注入 → LLM最終回答
```

---

## Obsidian知識ベース（Second Brain）

取引ログを生きたWikiに自動昇華させる3層構造。

```
data/knowledge_base/
├── obsidian_logs/          # 生ログ（BUY/SELL時に自動生成）
│   └── Log_YYYYMMDD_TICKER_ACTION.md
└── wiki/
    ├── INDEX.md            # 全体目次（自動再生成）
    ├── tickers/            # 銘柄別ページ（トレード履歴・評価変遷）
    │   ├── AAPL.md
    │   └── NVDA.md
    ├── concepts/           # 投資コンセプトページ（自動抽出）
    │   ├── macd_golden_cross.md
    │   └── thesis_driven_trading.md
    └── log.md              # Ingestログ（処理済みファイル名を記録）
```

`server_librarian.py --ingest` を実行するとobsidian_logsの未処理ログを検出し、ticker/conceptページを更新・INDEX.mdを再生成します。処理済みファイルはファイル名で管理するためタイミングによる取りこぼしがありません。

---

## インフラ構成

```
┌─────────────────────────────────────────────────────┐
│  メインサーバー（uema2lab-search）                    │
│                                                     │
│  cron 07:30 JST → main.py --screen --hybrid         │
│  cron 23:00 JST → auto_push.sh（git自動コミット）    │
│                                                     │
│  • S&P500日次スクリーニング                           │
│  • 5エージェント合意パイプライン                      │
│  • ChromaDB永続ベクトルストア                         │
│  • 訓練データ蓄積（data/training/）                  │
└─────────────────────────────────────────────────────┘
                        │ Ollama API
                        ▼
┌─────────────────────────────────────────────────────┐
│  GPU推論サーバー（ASRock / Radeon RX 5700 XT）        │
│                                                     │
│  • Ollama — CriticAgent用ローカルLLM独立審査          │
│  • RTCアラームで早朝自動電源ON                        │
│  • 処理完了後は自動スリープ                           │
└─────────────────────────────────────────────────────┘
                        │ git push
                        ▼
              github.com/sarada1001/ai-investor-bot
```

---

## ディレクトリ構造

```
ai-investor-bot/
├── main.py                      # メインエンジン（ステージゲートパイプライン）
├── server_librarian.py          # 日報生成 & Wikiインジェスト
├── run_pipeline.py              # cronトリガー用スクリプト
├── build_corpus.py              # S&P500金融コーパスビルダー
├── dashboard.py                 # Streamlit可視化ダッシュボード
├── auto_push.sh                 # Git自動コミット+プッシュ
├── agents/
│   ├── fundamental_agent.py     # Multi-HyDE RAG + EDGAR
│   └── exit_agent.py            # ExitAgent（保有ポジション監視）
├── skills/
│   ├── screener.py              # S&P500スクリーナー（LLM不使用）
│   ├── technical_calc.py        # テクニカル指標計算
│   ├── news_monitor.py          # ニュース取得・センチメント分析
│   ├── macro_monitor.py         # マクロ指標取得
│   ├── social_monitor.py        # SNSセンチメント取得
│   ├── risk_calculator.py       # ポジションサイジング
│   ├── rag_search.py            # ChromaDB検索
│   ├── edgar_fetcher.py         # SEC EDGAR自律取得
│   ├── alpaca_trade.py          # Alpaca発注
│   ├── portfolio_monitor.py     # ポートフォリオ追跡
│   └── training_data_collector.py
├── tools/
│   ├── critic_agent.py          # CriticAgent（Ollama独立審査）
│   ├── auto_logger.py           # Obsidianログ自動生成
│   ├── alpaca_client.py         # Alpacaクライアント
│   └── create_reflexion_log.py
├── scripts/
│   ├── lint_wiki.py             # Wikiヘルスチェック
│   └── run_ablation_test.py     # アブレーションテスト
├── data/
│   ├── knowledge_base/          # 銘柄別JSONコーパス + Obsidian Wiki
│   ├── screener/cache.json      # スクリーナー当日キャッシュ
│   ├── training/                # 推論トレース蓄積（JSONL）
│   └── portfolio.json           # 保有ポジション管理
├── bbs/                         # エージェント間通信ログ
└── requirements.txt
```

---

## セットアップ

### 1. クローン & インストール

```bash
git clone https://github.com/sarada1001/ai-investor-bot.git
cd ai-investor-bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 環境変数

```bash
cp .env.example .env
```

```env
GOOGLE_API_KEY=          # Google AI Studio（Gemini 2.0/2.5 Flash）
ALPACA_API_KEY=          # Alpaca Markets（ペーパーまたはライブ）
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=         # https://paper-api.alpaca.markets（ペーパー）
LINE_ACCESS_TOKEN=       # LINE Messaging API
LINE_USER_ID=
OLLAMA_ENDPOINT=         # http://<GPU-server-IP>:11434
ALPHA_VANTAGE_API_KEY=   # ニュース取得（オプション）
FINNHUB_API_KEY=         # ニュース取得（オプション）
```

### 3. 金融コーパスのビルド

```bash
python build_corpus.py   # S&P500全503銘柄（再開対応・約20分）
```

### 4. 動作確認

```bash
# トークン消費ゼロのモックテスト
python main.py --screen --mock

# ドライラン（実データ・発注なし）
python main.py --screen --dry-run --top-n 3
```

---

## 技術スタック

| カテゴリ | 技術 |
|---|---|
| LLM（クラウド） | Google Gemini 2.0/2.5 Flash（`langchain-google-genai`） |
| LLM（ローカル） | Ollama（CriticAgent用独立審査） |
| 埋め込み | `intfloat/multilingual-e5-small`（ChromaDB） |
| ベクトルDB | ChromaDB（PersistentClient） |
| RAG手法 | Multi-HyDE（複数仮説による仮説的ドキュメント埋め込み） |
| 金融データ | yfinance（OHLCV + ファンダメンタルズ） |
| ニュース | Alpha Vantage / Finnhub |
| 取引執行 | Alpaca Markets API（`alpaca-py`） |
| 通知 | LINE Messaging API |
| 知識管理 | Obsidian Markdown（自動Wiki生成） |
| ダッシュボード | Streamlit |
| スケジューリング | cron（JST対応） |

---

## ロードマップ

- [ ] セマンティックチャンキング — 段落レベルのチャンク分割でRAG精度向上
- [ ] 推論ログフィードバックループ — エージェントトレースをローカルLLMで蒸留してナレッジベースに反映
- [ ] バックテスト強化 — 過去データでのシミュレーション精度向上
- [ ] ポートフォリオ最適化 — 複数銘柄間のリスク分散ロジック追加
- [ ] FinanceBench評価 — RAG検索品質の体系的ベンチマーク

## 🔄 Development History
- 📅 **2026-05-12 14:00:01** | 🛠️ **内容:** `auto-backup: 2026-05-12 14:00:01 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/b6fc1f72b42bdd24414d8b82938ef65f1dabd0ce)
- 📅 **2026-05-11 14:00:01** | 🛠️ **内容:** `auto-backup: 2026-05-11 14:00:01 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/bee744929dc3721eb030f2103d21f3f14c51dd32)
- 📅 **2026-05-08 14:00:01** | 🛠️ **内容:** `auto-backup: 2026-05-08 14:00:01 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/acff6fedf8a1a7c9b1f6c828b044e557a4de51ff)
- 📅 **2026-05-07 14:00:01** | 🛠️ **内容:** `auto-backup: 2026-05-07 14:00:01 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/105e1349850b71854bdfca75bd82323d50eab0df)
