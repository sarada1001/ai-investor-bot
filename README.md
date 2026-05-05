# exa-investor — 自律型金融AIエージェント

> **スイングトレード意思決定のための分散推論システム**  
> Multi-HyDE RAG・ChromaDB・3層分散コンピューティングインフラによるマルチエージェント合意アーキテクチャ。

---

## 概要

**exa-investor** は情報科学の研究を目的として設計された、研究志向の自律型金融AIエージェントです。  
S&P 500の金融コーパスを自動収集してローカルナレッジベースにベクトル化し、厳格に権限スコープを分離した5つの専門エージェントがパイプラインを実行することで、完全な推論ログ付きのコンプライアンス準拠スイングトレード判断を生成します。

本プロジェクトの長期目標は、エージェントの推論トレースを蓄積し、ローカルLLM（Ollama上のLlama 3.1）を通じて将来の推論サイクルへフィードバックする自己改善型RAGパイプラインの構築です。これにより、外部API依存なしの継続的な知識蒸留を実現します。

---

## システムアーキテクチャ

共有Gitリポジトリと JST/UTC対応のcronジョブによって協調する、物理的に独立した3層コンピューティング構成で動作します。

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        TIER 1 — クラウドサーバー                        │
│                        (www.dmgpt.site / Linux)                        │
│                                                                         │
│   cron 07:30 JST    →   run_pipeline.py --hybrid                       │
│   cron 23:00 JST    →   auto_push.sh  (git commit + push to GitHub)    │
│                                                                         │
│   • S&P 500日次スクリーニング (yfinance + Gemini 2.5 Flash)             │
│   • 5エージェント合意パイプライン  →  BBS共有メモリ                      │
│   • 金融コーパス自動収集  (build_corpus.py, 503銘柄)                    │
│   • 訓練データ蓄積  (data/training/training_data.jsonl)                 │
└─────────────────────────────────────────────────────────────────────────┘
                                    │  GitHub (自動プッシュ)
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     TIER 2 — エッジコントローラー                        │
│                     (ThinkPad E16 / RAM 32 GB)                         │
│                                                                         │
│   • Librarian.py  — タスクオーケストレーション & クラウドログ同期        │
│   • ChromaDB  — 永続ベクトルストア (financial_corpusコレクション)        │
│   • RAG検索ホスト  (rag_test.py / rag_search スキル)                   │
│   • Streamlitダッシュボード  (dashboard.py)                             │
│   • JST対応cronによるGitHub自動プッシュ                                 │
└─────────────────────────────────────────────────────────────────────────┘
                                    │  推論リクエスト
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    TIER 3 — GPU推論ノード                               │
│                    (ASRock RX 5700 XT / 8 GB VRAM)                    │
│                                                                         │
│   • RTCスケジュール起動 (マザーボードによる早朝自動電源ON)               │
│   • Ollama + Llama 3.1  — オフライン推論 & 推論ログ分析                │
│   • 知識蒸留  →  ObsidianマークダウンVault（自動エクスポート）           │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## エージェントアーキテクチャ

5つの専門エージェントは共有 **BBS（掲示板システム）** テキストメモリのみを通じて通信します。各エージェントは他のエージェントへの直接参照を持たず、定義されたフェーズ順序に従ってBBSの読み書きを行います。

```
┌──────────────────────────────────────────────────────────────────────┐
│  フェーズ1 — 並列情報収集                                             │
│                                                                      │
│   NewsAgent            FundamentalAgent        TechnicalAgent        │
│   [news_monitor]       [rag_search]            [technical_calc]      │
│   RSS + LLM感情分析    Multi-HyDE × ChromaDB   RSI / MACD / MA25    │
│        │                      │                       │              │
│        └──────────────────────┴───────────────────────┘             │
│                               │                                      │
│                         BBS（共有メモリ）                             │
│                               │                                      │
│  フェーズ2 — 統合判断                                                 │
│                               │                                      │
│                         ManagerAgent                                 │
│                 [ニュース30% / FA 40% / TA 30%]                      │
│                               │                                      │
│  フェーズ3 — コンプライアンスゲート                                    │
│                               │                                      │
│                       ComplianceAgent                                │
│               [8ルール適用、REJECT / MODIFY / PASS]                  │
│                               │                                      │
│                    LINEプッシュ通知                                   │
└──────────────────────────────────────────────────────────────────────┘
```

### エージェント権限マトリクス

| エージェント | 役割 | 許可スキル |
|---|---|---|
| **NewsAgent** | Google News RSSを取得し、LLMによる感情分類（ポジティブ / ニュートラル / ネガティブ）を実行 | `news_monitor` |
| **FundamentalAgent** | Multi-HyDEで金融コーパスを検索し、貸借対照表の質と成長性を評価 | `rag_search` |
| **TechnicalAgent** | yfinanceで日次OHLCVを取得し、RSI・MACD・MA25・出来高スパイクスコアを算出 | `technical_calc` |
| **ManagerAgent** | BBSを読み込み、加重合意によるBUY/HOLD/SELL判断と信頼度スコアを生成 | _（BBS読み取り専用）_ |
| **ComplianceAgent** | 8つのハードコンプライアンスルールを適用し、非準拠判断をREJECTまたはMODIFY | _（BBS読み取り専用）_ |
| **ExitAgent** | オープンポジションを監視し、ストップロスまたは利確の決済をトリガー | `portfolio_monitor` |

### Multi-HyDE

**仮説的ドキュメント埋め込み（HyDE）** を複数仮説生成に拡張した手法です。  
クエリごとに、実際の決算書に掲載されるような仮説的な文書抜粋を3つLLMに生成させ、元のクエリとともにすべて埋め込みます。これにより、金融ドメイン固有の専門用語に対する再現率が大幅に向上します。

---

## 機能

### 金融ナレッジベース（`build_corpus.py`）
- WikipediaのS&P 500リストからティッカーユニバースを取得（503銘柄）
- yfinanceで各銘柄の `sector`・`industry`・`longBusinessSummary` を取得
- **メタデータ / コンテンツを明示的に分離**したJSON形式で `data/knowledge_base/` に1銘柄1ファイル保存 — ChromaDBのメタデータフィルタリングに対応
- **再開機能**: 再実行時は取得済み銘柄をスキップ。いつでも安全に中断可能
- **IPバン対策**: リクエスト間にランダムな `sleep(1〜3秒)` と `tqdm` プログレスバーを実装

```json
{
  "metadata": { "ticker": "NVDA", "name": "NVIDIA Corporation",
                "sector": "Technology", "industry": "Semiconductors", ... },
  "content":  { "long_business_summary": "NVIDIA Corporation operates as a data center ..." }
}
```

### RAGプロトタイプ（`rag_test.py`）
- `data/chroma_db/` に `PersistentClient` ChromaDBを初期化
- `all-MiniLM-L6-v2`（ChromaDB標準EF）で全 `long_business_summary` を埋め込み
- **セマンティック検索**と**メタデータフィルタリングによるハイブリッドクエリ**をサポート
- 再開対応のバッチアップサート（既存ドキュメントIDはスキップ）

### Streamlitダッシュボード（`dashboard.py`）
- 銘柄ごとのエージェント推論プロセスをリアルタイム可視化
- エージェントごとにカラーコードされたバッジ付きBBSセッションログブラウザ
- 信頼度スコアゲージ、コンプライアンス判断タイムライン、オープンポジションモニター

### Git自動パイプライン（`auto_push.sh`）
- `git status -s` で未コミット変更を検出。変更がなければスキップ
- タイムスタンプ付きメッセージでコミットし、`README.md` の開発履歴にエントリを追記
- `git push` の終了コードを確認。失敗時は誤検知成功ではなく `ERROR` をログに記録
- **JST 23:00**（UTC `0 14 * * 1-5`）にcronで実行

---

## コンプライアンスルール

`ComplianceAgent` は以下の8つのルールを機械的に適用します。違反があれば、判断が実行レイヤーに到達する前に `REJECT` または `MODIFY` をトリガーします。

| ルールID | 制約内容 |
|---|---|
| RULE-01 | 1銘柄あたりの最大保有額は総資産の **20%** （集中リスク制限） |
| RULE-02 | 同一銘柄への **3営業日以内の連続BUYを禁止**（ナンピン防止） |
| RULE-03 | ストップロスはエントリー価格から **−8%** 以内に設定（ハードキャップ） |
| RULE-04 | 推奨保有期間は **20営業日以内** |
| RULE-05 | 信頼度スコア **50未満 → 強制HOLD** |
| RULE-06 | 対象銘柄にネガティブニュースが検出された場合、**BUYを禁止** |
| RULE-07 | 最大同時保有ポジション数 **4件**、総エクスポージャーは資産の **60%以内** |
| RULE-08 | **根拠のない憶測による判断を禁止** |

---

## ディレクトリ構造

```
exa-investor/
├── agents/                      # エージェント実装
│   └── fundamental_agent.py
├── skills/                      # スキルモジュール（独立・副作用なし）
│   ├── news_monitor.py          # RSSフェッチ + LLM感情分析
│   ├── rag_search.py            # Multi-HyDE × ChromaDB検索
│   ├── technical_calc.py        # RSI / MACD / MA25
│   ├── portfolio_monitor.py     # Alpacaポジションヘルスチェック
│   └── training_data_collector.py
├── rules/
│   └── swing_trade_rules.md     # 8つのコンプライアンスルール（機械可読）
├── data/
│   ├── knowledge_base/          # 銘柄別JSONコーパス（S&P 500、503銘柄）
│   ├── chroma_db/               # ChromaDB永続ベクトルストア
│   └── training/
│       └── training_data.jsonl  # エージェント推論トレース蓄積
├── bbs/                         # BBSセッションログ（エージェント共有メモリ）
│   └── YYYYMMDD_HHMMSS.json
├── main.py                      # フルパイプラインオーケストレーター
├── run_pipeline.py              # cronトリガーのハイブリッドスクリーニングパイプライン
├── build_corpus.py              # S&P 500金融コーパスビルダー
├── rag_test.py                  # ChromaDB RAG検索プロトタイプ
├── dashboard.py                 # Streamlit可視化ダッシュボード
├── auto_push.sh                 # Git自動コミット + プッシュ（JST対応）
└── requirements.txt
```

---

## 技術スタック

| カテゴリ | 技術 |
|---|---|
| LLM（クラウド） | Google Gemini 2.5 Flash（`langchain-google-genai`） |
| LLM（ローカル） | Llama 3.1 via Ollama（GPUノード） |
| 埋め込み | `all-MiniLM-L6-v2`（ChromaDB標準）/ `intfloat/multilingual-e5-small` |
| ベクトルDB | ChromaDB（PersistentClient） |
| RAG手法 | Multi-HyDE（複数仮説による仮説的ドキュメント埋め込み） |
| 金融データ | yfinance（日次OHLCV + ファンダメンタルズ） |
| ニュース | Google News RSS（`feedparser`） |
| 取引執行 | Alpaca Markets API（`alpaca-py`） |
| 通知 | LINE Messaging API |
| ダッシュボード | Streamlit + Plotly |
| 知識Vault | Obsidian Markdown（GPUノード自動エクスポート） |
| スケジューリング | cron（UTC対応・JST整合） |

---

## セットアップ

### 1. クローン & インストール

```bash
git clone https://github.com/sarada1001/ai-investor-bot.git
cd exa-investor
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 環境変数

```bash
cp .env.example .env   # 各キーを入力してください
```

```env
GOOGLE_API_KEY=          # Google AI Studio (Gemini 2.5 Flash)
LINE_ACCESS_TOKEN=       # LINE Messaging API
LINE_USER_ID=
ALPACA_API_KEY=          # Alpaca Markets（ペーパーまたはライブ）
ALPACA_SECRET_KEY=
```

### 3. 金融コーパスのビルド

```bash
# テスト実行（5銘柄）
python build_corpus.py          # TEST_MODE = True（デフォルト）

# フル実行 — S&P 500（約20分、再開対応）
# build_corpus.py の TEST_MODE を False に変更してから実行:
python build_corpus.py
```

### 4. ChromaDBへのインポート & 検索確認

```bash
python rag_test.py
```

### 5. エージェントパイプラインの実行

```bash
python run_pipeline.py --hybrid   # スクリーニング + エージェントパイプライン
streamlit run dashboard.py        # 可視化ダッシュボード
```

---

## ロードマップ

- [ ] **セマンティックチャンキング** — ドキュメント全体の埋め込みを段落レベルのチャンクに置き換え、検索粒度を向上
- [ ] **Multi-HyDEの拡張** — S&P 500コーパスへの既存Multi-HyDE手法の適用（現在はIR PDFの検索にのみ使用）
- [ ] **Self-Refine / Reflexion** — コンプライアンスゲート前の推論品質向上のためのエージェント間ディベート・自己批判ループ
- [ ] **推論ログフィードバックループ** — 日次エージェントトレースをLlama 3.1（GPUノード）で蒸留し、ChromaDBナレッジベースへ継続的に反映
- [ ] **FinanceBench評価** — FinanceBench QAデータセットによるRAG検索品質の体系的ベンチマーク

---

## 🚀 本日のピック銘柄

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

## 🔄 開発履歴
- 📅 **2026-05-04 09:27:46** | 🛠️ **内容:** `auto-backup: 2026-05-04 09:27:46 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/13ff9292a4ae19fdd8ec8522ac84e7c8902604c0)
- 📅 **2026-05-01 23:00:01** | 🛠️ **内容:** `auto-backup: 2026-05-01 23:00:01 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/9e699096cffdb4900efe87a14fc8448dc2ca2c32)
- 📅 **2026-05-01 14:28:10** | 🛠️ **内容:** `auto-backup: 2026-05-01 14:28:10 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/fadd468aca11b8174e413a31cff29f39e610f41e)

## 🔄 Development History
- 📅 **2026-05-05 14:00:01** | 🛠️ **内容:** `auto-backup: 2026-05-05 14:00:01 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/62e657e98d43edc2bd7d795f67db6cb9dddef077)
- 📅 **2026-05-05 08:47:07** | 🛠️ **内容:** `auto-backup: 2026-05-05 08:47:07 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/ceff7624e2f42ed1f4fb642fdab8e14e3b4592c4)
- 📅 **2026-05-05 08:33:47** | 🛠️ **内容:** `auto-backup: 2026-05-05 08:33:47 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/5236a3c315e28d5688b9415e6e0e1a960e2ccf8a)
- 📅 **2026-05-04 09:42:44** | 🛠️ **内容:** `auto-backup: 2026-05-04 09:42:44 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/27dd51275a5ba75e9ba71020f84b56440ac93902)

## 🚀 Latest Daily Pick

> 最終更新: 2026-05-04 22:33

### 本日の精鋭3銘柄 (2026-05-04)

| # | ティッカー | スコア | 価格 | 選出理由 |
|---|-----------|--------|------|---------|
| 1 | **NCLH** | 0.6717 | $17.20 | RSI=26.2（売られすぎ） / 出来高2.9倍スパイク / MA20から10.8%下方乖離 |
| 2 | **FDX** | 0.6500 | $357.80 | 出来高3.8倍スパイク / MA20から6.3%下方乖離 |
| 3 | **TSCO** | 0.6234 | $32.31 | RSI=6.9（売られすぎ） / 出来高1.5倍スパイク / MA20から20.2%下方乖離 |

#### 指標内訳

| ティッカー | RSI | VOL倍率 | VOLスコア | MAスコア | 総合スコア |
|-----------|-----|--------|----------|--------|----------|
| NCLH | 26.2 | 2.89x | 0.944 | 1.000 | 0.6717 |
| FDX | 36.1 | 3.80x | 1.000 | 1.000 | 0.6500 |
| TSCO | 6.9 | 1.52x | 0.260 | 1.000 | 0.6234 |

### 本日の Exit 判断

- **NVDA**: ストップロス到達（現在 $115.0 < SL $118.0） (損益: -4.17%)
