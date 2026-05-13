# ai-investor-bot — 自律型スイングトレードAIエージェント

> S&P500を対象にした、マルチエージェント合意方式の自律売買システム。  
> ステージゲート型パイプライン・Multi-HyDE RAG・Alpaca自動発注・Obsidian知識ベース連携。

---

## 目次

1. [システム全体アーキテクチャ](#システム全体アーキテクチャ)
2. [Phase 1〜4 パイプライン詳細](#phase-14-パイプライン詳細)
3. [エージェント一覧](#エージェント一覧)
4. [安全装置（多層防御）](#安全装置多層防御)
5. [日常運用ガイド](#日常運用ガイド)
6. [monitor.py — ターミナルTUI](#monitorpy--ターミナルtui)
7. [dashboard.py — Streamlit Web UI](#dashboardpy--streamlit-web-ui)
8. [ライブ取引のオンにする手順](#ライブ取引をオンにする手順)
9. [セットアップ](#セットアップ)
10. [コマンドリファレンス](#コマンドリファレンス)
11. [知識ベース・Wiki管理](#知識ベースwiki管理)
12. [インフラ構成](#インフラ構成)
13. [技術スタック](#技術スタック)
14. [ディレクトリ構造](#ディレクトリ構造)
15. [ロードマップ](#ロードマップ)

---

## システム全体アーキテクチャ

```
┌──────────────────────────────────────────────────────────────────┐
│                     ECC スイングトレードエンジン                    │
│                                                                  │
│  ① S&P500スクリーナー（LLM不使用・テクニカルスコア）                │
│    → 503銘柄 → 上位5銘柄に絞込（当日キャッシュあり）               │
│                                                                  │
│  ② Phase 1 — 安価スキャン（全エージェント並列実行）                 │
│                                                                  │
│   TechnicalAgent   NewsAgent   MacroAgent   SocialAgent          │
│   RSI/MACD/SMA     ニュース     SPY/VIX      SNSセンチメント       │
│        └──────────────┴─────────────┴────────────┘              │
│                       BBS（共有メモリバス）                        │
│                               │                                  │
│  ③ Gate: Macro NEGATIVE → 即HOLD / Tech+News双方NEUTRAL → 即HOLD  │
│                               │                                  │
│  ④ Phase 2 — ファンダメンタル分析（Gate通過時のみ）                  │
│                                                                  │
│           FundamentalAgent（Multi-HyDE RAG + EDGAR）             │
│                               │                                  │
│  ⑤ Phase 3 — 最終判断                                             │
│                                                                  │
│    ManagerAgent → CriticAgent（Ollama）→ 加重スコア算出            │
│                               │                                  │
│  ⑥ Phase 4 — リスク計算（STRONG BUY時のみ）                        │
│                                                                  │
│        RiskAgent（Fixed Fractional + Kelly Criterion）           │
│                               │                                  │
│  ⑦ 安全装置チェック                                                │
│     LiveTradingGate → TradeGuard → CircuitBreaker               │
│                               │                                  │
│              Alpaca発注 / LINE通知 / Obsidianログ記録              │
└──────────────────────────────────────────────────────────────────┘
```

### STRONG BUY 判定条件（すべて満たすこと）

| 条件 | 詳細 |
|---|---|
| 加重スコア ≥ 0.60 | FA×0.40 / Tech×0.20 / Macro×0.20 / News×0.10 / Social×0.10 |
| Fundamental > 0 | 必須（ゼロまたはネガティブなら発注なし） |
| Technical ≥ 0 | ネガティブなら発注なし |
| News ≥ 0 | ネガティブなら発注なし |
| Macro ≥ 0 | NEGATIVE時は強制HOLD |

---

## Phase 1〜4 パイプライン詳細

### Phase 1 — 安価スキャン（並列実行）

LLM呼び出しを最小化しながら4エージェントが独立してシグナルを生成します。各エージェントの結果はBBS（Bulletin Board System: 共有JSON辞書）に書き込まれます。

| エージェント | 処理内容 | BBSキー |
|---|---|---|
| TechnicalAgent | RSI・MACD・SMA25乖離・出来高比を算出してLLM評価 | `technical_analysis` |
| NewsAgent | Alpha Vantage / Finnhubからニュース取得 → センチメント判定 | `news_analysis` |
| MacroAgent | SPY・VIXでマクロ環境評価 | `macro_analysis` |
| SocialAgent | SNSセンチメント + hype_score算出 | `social_analysis` |

**ステージゲート（Gate）:**
- `MacroAgent` が **NEGATIVE** → 全銘柄即HOLD（マクロ悪化時のブレーキ）
- `TechnicalAgent` と `NewsAgent` の両方が **NEUTRAL** → コスト削減HOLD

### Phase 2 — ファンダメンタル分析

Gate通過銘柄のみ実行。**Multi-HyDE RAG**を採用:

```
ユーザークエリ
  └→ LLMで仮説ドキュメント×3生成（「実際の決算書に書かれているであろう回答」）
       └→ [クエリ + 仮説×3] でChromaDB検索
            └→ 上位チャンクをコンテキストに注入 → LLM最終回答
```

加えて **EDGAR自律取得**: 四半期データが7日以上古い場合、SEC EDGARから最新の10-Q/10-Kを自動ダウンロードしてChromaDBを更新します。

### Phase 3 — 最終判断

1. **ManagerAgent**: BBSデータを集約し加重スコアを算出。SNS買い煽り（hype_score ≥ 0.7）検出時はペナルティ適用
2. **CriticAgent**: OllamaローカルLLMが独立審査。`OVERRIDE`可能（強制HOLD/強制BUY）
3. **AuditAgent**: 過去トレード勝率を評価し、成績不振エージェント（勝率<40%、取引数≥20）を`SUSPENDED`に設定してウェイトをゼロに

### Phase 4 — リスク計算

STRONG BUY確定後のみ実行:
- **Fixed Fractional**: 口座残高の2%リスクから推奨株数を算出
- **Kelly Criterion**: 期待勝率・リターン比からKelly係数で算出
- 上記2手法の小さい方を採用（保守的なサイジング）
- ストップロス: ATR × 2 下方
- 利確目標: ATR × 4 上方（RR 1:2）

---

## エージェント一覧

| エージェント | 役割 | 実装場所 |
|---|---|---|
| **TechnicalAgent** | RSI・MACD・SMA25乖離・出来高比 → LLM評価 | `engine/agent_wrappers.py` |
| **NewsAgent** | ニュース取得・センチメント分析 | `engine/agent_wrappers.py` |
| **MacroAgent** | SPY/VIXマクロ環境評価（NEGATIVE時ブレーキ） | `engine/agent_wrappers.py` |
| **SocialAgent** | SNSセンチメント + 買い煽りペナルティ | `engine/agent_wrappers.py` |
| **FundamentalAgent** | Multi-HyDE RAG + EDGAR自律取得 | `agents/fundamental_agent.py` |
| **ManagerAgent** | BBS集約・加重スコア算出・最終判断 | `engine/agent_wrappers.py` |
| **CriticAgent** | Ollama独立審査・OVERRIDE機能 | `tools/critic_agent.py` |
| **RiskAgent** | Fixed Fractional + Kelly Criterion | `engine/agent_wrappers.py` |
| **ExitAgent** | 保有ポジション監視（+10%利確 / -5%損切 / THESIS_BROKEN） | `agents/exit_agent.py` |
| **AuditAgent** | エージェント勝率評価・SUSPENDED管理 | `agents/audit_agent.py` |

### AuditAgent — メタ評価ループ

| パラメータ | 値 |
|---|---|
| 評価最低取引数 | 20回（Grace Period: それ未満は評価保留） |
| SUSPENDED閾値 | 勝率 < 40% |
| 復帰閾値 | 勝率 ≥ 50% |
| SUSPENDED時の動作 | ウェイト=0（発言権ミュート）、Shadow Modeで記録継続 |

---

## 安全装置（多層防御）

本システムは4層の安全装置を持ちます。**1つでもNGなら発注ブロック**。

```
発注フロー
  │
  ├─① LiveTradingGate（ライブ取引二段階認証）
  │    ALPACA_PAPER_TRADING=false かつ 意思ファイル有効 かつ 24h未失効
  │
  ├─② TradeGuard（発注ガードレール）
  │    日次BUY上限・同時保有銘柄数上限・ポジション比率上限
  │
  ├─③ CircuitBreaker（ドローダウン自動停止）
  │    日次-5% → SOFT_TRIP / 高値比-10% → HARD_TRIP
  │
  └─④ DryRun/Mock フラグ（テスト時）
```

### ① LiveTradingGate

| 条件 | 内容 |
|---|---|
| 環境変数 | `ALPACA_PAPER_TRADING=false` が必須 |
| 意思ファイル | `data/live_trading_enabled.json` が存在する |
| 有効期限 | 認証から24時間（毎取引日に再認証必須） |
| APIキー照合 | 意思ファイルのkey_suffix ↔ 現在のAPIキー末尾4桁が一致 |

有効化・無効化:
```bash
python main.py --enable-live   # インタラクティブウィザード起動
python main.py --disable-live  # 即座に無効化（意思ファイル削除）
```

### ② TradeGuard

デフォルト設定（`data/trade_guards.json` で変更可）:

| ガードレール | デフォルト値 |
|---|---|
| 日次BUY上限 | 3回/日 |
| 同時保有銘柄数上限 | 5銘柄 |
| 1銘柄の最大ポジション比率 | 口座の20% |

### ③ CircuitBreaker

| トリップ | 条件 | 影響 | 解除方法 |
|---|---|---|---|
| SOFT_TRIP | 日次ドローダウン ≥ -5% | 当日の新規BUYを禁止 | 翌日自動リセット |
| HARD_TRIP | 高値比ドローダウン ≥ -10% | 全BUYを禁止 | `manual_reset(level="hard")` のみ |

状態確認:
```python
from tools.circuit_breaker import CircuitBreaker
cb = CircuitBreaker()
print(cb.status)  # "OPEN" | "SOFT_TRIP" | "HARD_TRIP"
```

HARD_TRIP手動解除（緊急時のみ）:
```python
cb.manual_reset(level="hard")
```

---

## 日常運用ガイド

### 1. ペーパートレードの起動（推奨ルーティン）

```bash
# プリフライトチェック（API接続・環境変数を確認）
./run_paper.sh --preflight

# スクリーニング + 1サイクル実行（ペーパー発注あり）
./run_paper.sh --screen

# 24時間デーモン起動（1時間ごとに評価 + LINE通知）
./run_paper.sh --daemon --screen --notify-line
```

### 2. ドライラン（発注なし・ログ確認）

```bash
# ドライラン（実データ・発注なし）
python main.py --screen --dry-run --top-n 3

# トークン消費ゼロのモックテスト
python main.py --screen --mock
```

### 3. ExitAgent（保有ポジション監視）

保有ポジションの利確・損切は `ExitAgent` が自動で監視します。デーモン起動中は各サイクルで自動実行されますが、手動実行も可能:

```bash
# ExitAgent単体実行（保有ポジションのみ評価）
python -c "from agents.exit_agent import run_exit_cycle; run_exit_cycle()"
```

出口条件:
| 条件 | 動作 |
|---|---|
| 含み益 ≥ +10% | 利確売り |
| 含み損 ≤ -5% | 損切り |
| THESIS_BROKEN | FundamentalAgentが投資根拠崩壊を判定 → 即売り |

### 4. 急落エントリースキャン（DipScan）

デーモンモード稼働中、メインサイクルのスリープ間（15分ごと）に急落エントリー機会を自動スキャンします:
- 対象: 当日スクリーニング上位銘柄
- 閾値: 当日始値から -3% 以上の下落
- アラート: LINE通知（`--notify-line` 指定時）

### 5. 日報・Wiki更新

```bash
# 日報生成（latest_summary.md に出力）
python server_librarian.py

# 取引ログをWikiに反映（Obsidian連携）
python server_librarian.py --ingest

# Wikiヘルスチェック（リンク切れ・孤児ページ・矛盾検出）
python scripts/lint_wiki.py
```

### 6. アブレーション実験（エージェント除外）

```bash
# FundamentalAgentを除外してパフォーマンス比較
python main.py --screen --dry-run --exclude FundamentalAgent

# SocialAgentを除外
python main.py --screen --dry-run --exclude SocialAgent
```

---

## monitor.py — ターミナルTUI

SSH/tmux環境での運用に最適化されたリアルタイム監視ダッシュボード。

```
┌─ 💼 ポートフォリオ P&L ──────────────┐  ┌─ 🤖 エージェント健全性 ─────────────────┐
│ 銘柄  エントリ  現在値  株数  含み損益  │  │ エージェント  ステータス  勝率  最終評価  │
│ AAPL  $185.00  $196.50  10   +$115   │  │ Technical    ✅ ACTIVE  65%   13:00     │
│ NVDA  $420.00  $410.00   5   -$50    │  │ Fundamental  ✅ ACTIVE  72%   13:00     │
└──────────────────────────────────────┘  └──────────────────────────────────────────┘
┌─ 📡 スクリーナー（最新キャッシュ） ───┐  ┌─ 📋 最新トレード判断 ──────────────────┐
│ #  銘柄  Score  RSI  Momentum  VolSpike│  │ 日付  銘柄  判断          スコア  根拠   │
│ 1  NVDA  10    32.1  +2.5%   2.3x    │  │ 05-13 AAPL 🚀 STRONG BUY +0.720       │
└──────────────────────────────────────┘  └──────────────────────────────────────────┘
```

### 起動方法

```bash
# デフォルト（30秒更新）
python monitor.py

# 更新間隔を変更
python monitor.py --interval 60

# 1回だけ表示して終了（ログ確認・パイプ処理用）
python monitor.py --once

# tmux でバックグラウンド起動（推奨）
tmux new-session -d -s monitor 'python monitor.py'
tmux attach -t monitor   # アタッチ
# Ctrl+B, D でデタッチ（プロセスは継続）
```

### 表示パネルの説明

| パネル | 内容 | データソース |
|---|---|---|
| 💼 ポートフォリオ P&L | OPENポジションの含み損益（yfinanceでリアルタイム取得） | `data/portfolio.json` |
| 🤖 エージェント健全性 | 各エージェントのステータス・勝率・最終評価時刻 | `data/agent_status.json` |
| 📡 スクリーナー | 最新スクリーニング結果（RSI・モメンタム・VolSpike） | `data/screener/intraday_cache.json` |
| 📋 最新トレード判断 | 直近5件のトレード判断・スコア・根拠 | `data/training/training_data.jsonl` |

**STALEアラート**: エージェントの最終評価から30分以上経過すると `⚠️ STALE` と表示されます（デーモンが停止している可能性）。

---

## dashboard.py — Streamlit Web UI

ブラウザからアクセスできるリッチな可視化ダッシュボード。

```bash
# 起動（デフォルト: http://localhost:8501）
streamlit run dashboard.py

# ポート指定
streamlit run dashboard.py --server.port 8502

# ヘッドレス環境（SSHトンネル経由でアクセス）
streamlit run dashboard.py --server.headless true
```

機能:
- ポートフォリオ P&L サマリーとチャート
- エージェント健全性ダッシュボード
- 60秒自動リフレッシュ
- トレード履歴テーブル
- スクリーナー結果のヒートマップ

---

## ライブ取引をオンにする手順

> **警告**: ライブ取引では実際の資金が動きます。手順を正確に守ってください。

### ステップ 1: .env の設定

```env
# ペーパーモードをオフにする
ALPACA_PAPER_TRADING=false

# ライブ用APIキーに切り替え
ALPACA_API_KEY=your_live_api_key
ALPACA_SECRET_KEY=your_live_secret_key
ALPACA_BASE_URL=https://api.alpaca.markets   # ライブエンドポイント
```

### ステップ 2: プリフライトチェック

```bash
./run_paper.sh --preflight
```

すべての項目が PASS になることを確認します。

### ステップ 3: ライブ取引有効化ウィザード

```bash
python main.py --enable-live
```

ウィザードが表示されます:
```
================================================================
  ⚠️  ライブ取引有効化ウィザード
================================================================

  ⚠️  本操作により実際の資金を使った取引が有効になります。
  対象 API Key : ****XXXX
  有効期限     : 24 時間（毎取引日に再認証が必要）

  確認のため "CONFIRM LIVE TRADING" と入力してください:
  > CONFIRM LIVE TRADING

  ✅ ライブ取引を有効化しました。
     有効期限 : 2026-05-14 09:00:00
```

### ステップ 4: 実行

```bash
# ライブ発注 + スクリーニング + LINE通知
python main.py --screen --notify-line

# デーモンモードで24時間稼働
python main.py --screen --daemon --notify-line
```

### ステップ 5: 無効化（いつでも即座に停止可能）

```bash
python main.py --disable-live
```

> **注意**: 有効期限は24時間です。毎取引日の開始前に `--enable-live` で再認証が必要です。

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
# エディタで .env を編集
```

```env
# 必須
GOOGLE_API_KEY=          # Google AI Studio（Gemini 2.5 Flash）
ALPACA_API_KEY=          # Alpaca Markets
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=         # https://paper-api.alpaca.markets（ペーパー）
ALPACA_PAPER_TRADING=true

# 通知
LINE_ACCESS_TOKEN=       # LINE Messaging API
LINE_USER_ID=

# オプション
OLLAMA_ENDPOINT=         # http://<GPU-server-IP>:11434（CriticAgent用）
ALPHA_VANTAGE_API_KEY=   # ニュース取得精度向上
FINNHUB_API_KEY=         # ニュース取得精度向上
SEC_USER_AGENT_NAME=     # EDGAR取得用（例: YourName）
SEC_USER_AGENT_EMAIL=    # EDGAR取得用（例: you@example.com）
```

### 3. 金融コーパスのビルド

S&P500全503銘柄の財務データをChromaDBに格納します（約20分）:

```bash
python build_corpus.py   # 再開対応（中断してもOK）
```

### 4. 動作確認

```bash
# トークン消費ゼロのモックテスト（システムフロー確認）
python main.py --screen --mock

# ドライラン（実データ・発注なし）
python main.py --screen --dry-run --top-n 3

# プリフライトチェック
./run_paper.sh --preflight
```

### 5. テスト実行

```bash
# ユニットテスト（CI環境と同等）
pytest tests/ -m "not integration and not slow"

# カバレッジ付き
pytest tests/ --cov=agents --cov=engine --cov=skills --cov=tools --cov-report=term-missing
```

---

## コマンドリファレンス

### main.py オプション

| オプション | 説明 |
|---|---|
| `--screen` | S&P500スクリーニング（日中動的）で上位N銘柄を自動選出 |
| `--screen-only` | スクリーニング結果のみ表示（AI分析スキップ） |
| `--top-n N` | スクリーニング選出銘柄数（デフォルト: 5） |
| `--ticker AAPL` | 単一銘柄指定 |
| `--tickers AAPL MSFT` | 複数銘柄ウォッチリスト |
| `--dry-run` | Alpaca発注をスキップ（ログのみ） |
| `--hybrid` | 全ステージリアル分析 + 発注スキップ（訓練データ蓄積に最適） |
| `--mock` | LLM/API呼び出しゼロ（システムフロー確認用） |
| `--notify-line` | 最終判断をLINE通知 |
| `--daemon` | 24時間自動取引ループ（市場閉場中は自動スリープ） |
| `--interval N` | デーモン評価間隔（秒、デフォルト: 3600=1時間） |
| `--exclude エージェント名` | 特定エージェントを除外（アブレーション実験用） |
| `--run-audit` | AuditAgentによるエージェント成績評価を実行 |
| `--enable-live` | ライブ取引有効化ウィザードを起動 |
| `--disable-live` | ライブ取引を即座に無効化 |

### スクリーナー動作モード

| モード | コマンド | 特徴 |
|---|---|---|
| 日次スクリーニング | `--screen`（デーモンなし） | 前場前に1回スクリーニング。当日キャッシュ利用 |
| 日中動的スクリーニング | `--screen`（デーモン起動中） | 15分ごとに急落スキャン、1時間ごとに再スクリーニング |
| 確認のみ | `--screen --screen-only` | AI分析せずスコア表示のみ |

---

## 知識ベース・Wiki管理

取引ログを生きたWikiに自動昇華させる3層構造（Andrej Karpathy方式）。

```
data/knowledge_base/
├── obsidian_logs/          # 生ログ（BUY/SELL時に自動生成）
│   └── Log_YYYYMMDD_TICKER_ACTION.md
└── wiki/
    ├── INDEX.md            # 全体目次（Ingestごとに自動再生成）
    ├── tickers/            # 銘柄別ページ（トレード履歴・評価変遷）
    │   ├── AAPL.md
    │   └── NVDA.md
    ├── concepts/           # 投資コンセプトページ（自動抽出）
    │   ├── macd_golden_cross.md
    │   └── sns_hype_trap.md
    └── log.md              # Ingestログ（処理済みファイル名を記録）
```

Wikiヘルスチェック項目:
1. リンク切れ検出（`[[リンク]]` の参照先が存在するか）
2. 孤児ページ検出（どこからもリンクされていないページ）
3. 矛盾検出（同一ティッカーのassessmentが複数ページで食い違い）
4. 鮮度チェック（last_updatedが7日以上古いティッカーページを警告）
5. 未リンクログ（obsidian_logsのログがどのティッカーページからも参照されていない）

---

## インフラ構成

```
┌─────────────────────────────────────────────────────┐
│  メインサーバー（uema2lab-search）                    │
│                                                     │
│  cron 09:00 JST → run_paper.sh --screen             │
│  cron 23:00 JST → auto_push.sh（git自動コミット）    │
│                                                     │
│  • S&P500日次スクリーニング                           │
│  • 5エージェント合意パイプライン                      │
│  • ChromaDB永続ベクトルストア（chroma_db_saved/）     │
│  • 訓練データ蓄積（data/training/training_data.jsonl）│
│  • tmux monitor.py（常時監視 TUI）                   │
└─────────────────────────────────────────────────────┘
                        │ Ollama API（CriticAgent）
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
              （GitHub Actions CI: nightly + PR）
```

### CI/CD（GitHub Actions）

- **トリガー**: `main`/`dev` へのpush、PR、毎朝00:00 UTC（ナイトリー）
- **テスト**: `pytest tests/ -m "not integration and not slow"`
- **カバレッジ**: `--cov-fail-under=30`（最低30%）
- **成果物**: `coverage.xml`（7日間保持）

---

## 技術スタック

| カテゴリ | 技術 |
|---|---|
| LLM（クラウド） | Google Gemini 2.5 Flash（`langchain-google-genai`） |
| LLM（ローカル） | Ollama（CriticAgent用独立審査） |
| 埋め込み | `intfloat/multilingual-e5-small`（ChromaDB） |
| ベクトルDB | ChromaDB（PersistentClient、`chroma_db_saved/`） |
| RAG手法 | Multi-HyDE（複数仮説による仮説的ドキュメント埋め込み） |
| 金融データ | yfinance（OHLCV + ファンダメンタルズ） |
| ニュース | Alpha Vantage / Finnhub |
| 財務データ | SEC EDGAR（自律取得・自動更新） |
| 取引執行 | Alpaca Markets API（`alpaca-py`） |
| 通知 | LINE Messaging API |
| 知識管理 | Obsidian Markdown（自動Wiki生成） |
| ターミナルUI | Rich（`monitor.py`） |
| Web UI | Streamlit（`dashboard.py`） |
| スケジューリング | cron（JST対応） |
| CI/CD | GitHub Actions |

---

## ディレクトリ構造

```
ai-investor-bot/
├── main.py                      # CLIエントリポイント（engine/に実装を委譲）
├── monitor.py                   # リアルタイムターミナルTUI（Rich）
├── dashboard.py                 # Streamlit Webダッシュボード
├── server_librarian.py          # 日報生成 & Wikiインジェスト
├── run_paper.sh                 # ペーパートレード起動スクリプト（推奨）
├── auto_push.sh                 # Git自動コミット+プッシュ
├── build_corpus.py              # S&P500金融コーパスビルダー
│
├── engine/                      # コアロジック
│   ├── trade_cycle.py           # メイントレードサイクル（Phase 1〜4）
│   ├── runner.py                # ウォッチリストサイクル & デーモンモード
│   ├── agent_wrappers.py        # 全エージェントの薄いラッパー
│   ├── bbs.py                   # 共有メモリバス（BBS）
│   ├── constants.py             # WEIGHTS・閾値・MOCK_BBS_DATA
│   ├── display.py               # ターミナル表示ヘルパー
│   ├── notify.py                # LINE通知
│   ├── trade_helpers.py         # ウェイト計算・Wiki参照
│   └── mock_helpers.py          # モックモード用ダミーデータ
│
├── agents/
│   ├── fundamental_agent.py     # Multi-HyDE RAG + EDGAR自律取得
│   ├── exit_agent.py            # ExitAgent（利確・損切・THESIS_BROKEN）
│   └── audit_agent.py           # AuditAgent（メタ評価・SUSPENDED管理）
│
├── skills/                      # データ取得・計算ロジック
│   ├── screener.py              # S&P500スクリーナー（LLM不使用）
│   ├── technical_calc.py        # テクニカル指標計算
│   ├── news_monitor.py          # ニュース取得・センチメント
│   ├── macro_monitor.py         # マクロ指標（SPY/VIX）
│   ├── social_monitor.py        # SNSセンチメント
│   ├── risk_calculator.py       # ポジションサイジング
│   ├── rag_search.py            # ChromaDB Multi-HyDE検索
│   ├── edgar_fetcher.py         # SEC EDGAR自律取得
│   ├── alpaca_trade.py          # Alpaca発注
│   ├── portfolio_monitor.py     # ポートフォリオ追跡
│   ├── portfolio_tracker.py     # P&L計算
│   ├── signal_scorer.py         # シグナルスコア正規化
│   ├── financial_data_loader.py # 財務データローダー
│   └── training_data_collector.py # 訓練データ収集
│
├── tools/                       # インフラツール
│   ├── live_trading_gate.py     # ライブ取引二段階認証ゲート
│   ├── circuit_breaker.py       # ドローダウン自動停止
│   ├── trade_guard.py           # 発注ガードレール（日次上限・比率）
│   ├── critic_agent.py          # CriticAgent（Ollama独立審査）
│   ├── alpaca_client.py         # Alpacaクライアント
│   ├── auto_logger.py           # Obsidianログ自動生成
│   └── create_reflexion_log.py  # Reflexionログ生成
│
├── scripts/
│   ├── preflight_check.py       # 本番前プリフライトチェック
│   ├── lint_wiki.py             # Wikiヘルスチェック
│   ├── run_ablation_test.py     # アブレーションテスト
│   ├── run_agent_exam.py        # エージェント試験
│   ├── run_backtest.py          # バックテスト
│   ├── run_performance_report.py # 成績レポート生成
│   └── run_weekly_reflection.py  # 週次自己反省ループ
│
├── tests/                       # ユニットテスト（GitHub Actions CI）
│   ├── test_circuit_breaker.py
│   ├── test_trade_guard.py
│   ├── test_gate_check.py
│   ├── test_dip_detector.py
│   ├── test_signal_scorer.py
│   └── ...（13テストファイル）
│
├── data/
│   ├── knowledge_base/          # Obsidian Wiki（3層構造）
│   ├── screener/cache.json      # スクリーナー当日キャッシュ
│   ├── screener/intraday_cache.json  # 日中スクリーニングキャッシュ
│   ├── training/training_data.jsonl  # 推論トレース蓄積
│   ├── portfolio.json           # 保有ポジション管理
│   ├── agent_status.json        # エージェント勝率・SUSPENDEDステータス
│   ├── circuit_breaker_state.json    # サーキットブレーカー状態
│   └── live_trading_enabled.json     # ライブ取引意思ファイル（24h）
│
├── bbs/                         # エージェント間通信ログ（セッション単位）
├── chroma_db_saved/             # ChromaDB永続ストア
├── .github/workflows/ci.yml     # GitHub Actions CI
├── requirements.txt             # 本番依存
├── requirements-ci.txt          # CI用依存（軽量）
├── pytest.ini                   # pytest設定
└── .env.example                 # 環境変数テンプレート
```

---

## ロードマップ

- [ ] セマンティックチャンキング — 段落レベルのチャンク分割でRAG精度向上
- [ ] 推論ログフィードバックループ — エージェントトレースをローカルLLMで蒸留してナレッジベースに反映
- [ ] バックテスト強化 — 過去データでのシミュレーション精度向上
- [ ] ポートフォリオ最適化 — 複数銘柄間のリスク分散ロジック追加
- [ ] FinanceBench評価 — RAG検索品質の体系的ベンチマーク
- [ ] HARD_TRIP自動通知 — サーキットブレーカー発動時のLINE即時アラート

## 🔄 Development History
- 📅 **2026-05-14 00:03:49** | 🛠️ **内容:** `auto-backup: 2026-05-14 00:03:49 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/d5f1c302a5c9b3748d8bd72698e85f27fe594e72)
- 📅 **2026-05-13 14:00:01** | 🛠️ **内容:** `auto-backup: 2026-05-13 14:00:01 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/77dfa3b8c4d57152502bd7348341b641af32f673)
- 📅 **2026-05-12 14:00:01** | 🛠️ **内容:** `auto-backup: 2026-05-12 14:00:01 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/b6fc1f72b42bdd24414d8b82938ef65f1dabd0ce)
- 📅 **2026-05-11 14:00:01** | 🛠️ **内容:** `auto-backup: 2026-05-11 14:00:01 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/bee744929dc3721eb030f2103d21f3f14c51dd32)
- 📅 **2026-05-08 14:00:01** | 🛠️ **内容:** `auto-backup: 2026-05-08 14:00:01 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/acff6fedf8a1a7c9b1f6c828b044e557a4de51ff)
- 📅 **2026-05-07 14:00:01** | 🛠️ **内容:** `auto-backup: 2026-05-07 14:00:01 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/105e1349850b71854bdfca75bd82323d50eab0df)
