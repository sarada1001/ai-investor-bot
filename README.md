# Autonomous Financial Multi-Agent System for Swing Trading

> **Multi-HyDE RAG × 5-Agent Consensus Architecture**  
> ニュース・ファンダメンタルズ・テクニカルの3軸をAIが自律分析し、コンプライアンス検閲を経て売買判断を下す金融マルチエージェントシステム。

---

## Overview

このシステムは **5つの専門AIエージェント** が共有メモリ（BBS）を介して順番に情報を書き込み、段階的に合意形成を行うことで、スイングトレード向けの投資判断を自動生成します。

各エージェントは **使用できるスキル（ツール）が厳格に制限** されており、権限外の操作を行えないアーキテクチャになっています（[ECC: everything-claude-code](https://github.com/anthropics/everything-claude-code) の設計思想を応用）。

```
┌─────────────────────────────────────────────────────────────────┐
│                     Phase 1: 情報収集                           │
│                                                                 │
│  NewsAgent        FundamentalAgent       TechnicalAgent         │
│  [news_monitor]   [rag_search]           [technical_calc]       │
│  RSS + LLM分析    Multi-HyDE RAG         RSI / MACD / MA25      │
│       │                  │                      │               │
│       └──────────────────┴──────────────────────┘              │
│                          │                                      │
│                    BBS (共有メモリ)                              │
│                          │                                      │
│                   Phase 2: 統合判断                             │
│                          │                                      │
│                    ManagerAgent                                  │
│              [ニュース30% / FA40% / TA30%]                       │
│                          │                                      │
│                   Phase 3: 検閲                                  │
│                          │                                      │
│                  ComplianceAgent                                 │
│              [8つのコンプライアンスルール適用]                   │
│                          │                                      │
│                     LINE 通知                                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Agents

| エージェント | 役割 | 使用スキル |
|---|---|---|
| **NewsAgent** | Google News RSSから最新記事を取得し、LLMでセンチメント（positive/negative/neutral）を判定 | `news_monitor` のみ |
| **FundamentalAgent** | ChromaDBに保存した決算資料を **Multi-HyDE** 手法で検索し、財務体質・成長性を分析 | `rag_search` のみ |
| **TechnicalAgent** | yfinanceで日足データを取得し、RSI・MACD・25日MAを計算してエントリータイミングを判断 | `technical_calc` のみ |
| **ManagerAgent** | 3エージェントの出力をBBSから読み込み、重み付き統合で売買アクション（BUY/HOLD/SELL）と確信度スコアを生成 | なし（BBS読み取りのみ） |
| **ComplianceAgent** | 8つのルールに照らしてManagerの判断を検閲。違反があればREJECT/MODIFY。最上位権限を持つ | なし（BBS読み取りのみ） |

### Multi-HyDE とは

**Hypothetical Document Embeddings (HyDE)** を複数生成する独自拡張手法。  
ユーザーの質問に対して「実際の決算資料にどう書かれているか」という仮説的回答を3パターンAIに生成させ、それを検索クエリに混ぜることで、専門用語のヒット率と検索精度を大幅に向上させています。

---

## Directory Structure

```
exa-investor/
│
├── skills/                      # 独立スキルモジュール（Skill Layer）
│   ├── __init__.py              # スキルレジストリ
│   ├── news_monitor.py          # RSS取得 + LLMセンチメント分析
│   ├── rag_search.py            # Multi-HyDE × ChromaDB 決算書検索
│   └── technical_calc.py        # RSI / MACD / 25日MA 計算
│
├── .agents/                     # エージェント定義（Agent Layer）
│   ├── news_agent.yaml          # 許可スキル: [news_monitor]
│   ├── fundamental_agent.yaml   # 許可スキル: [rag_search]
│   ├── technical_agent.yaml     # 許可スキル: [technical_calc]
│   ├── manager_agent.yaml       # 統合判断エージェント定義
│   └── compliance_agent.yaml    # 検閲エージェント定義
│
├── rules/
│   └── swing_trade_rules.md     # ComplianceAgentが遵守する8ルール
│
├── bbs/                         # BBS セッションログ（.gitignore対象）
│   └── YYYYMMDD_HHMMSS.json     # エージェント間共有メモリの永続化
│
├── main.py                      # オーケストレーション本体
├── requirements.txt
└── .env.example                 # 環境変数テンプレート
```

---

## Compliance Rules

ComplianceAgentは以下の8ルールを機械的に適用します。

| ルールID | 内容 |
|---|---|
| RULE-01 | 1銘柄あたり最大 **20%** まで（集中投資禁止） |
| RULE-02 | 3営業日以内の **連続BUY禁止**（ナンピン防止） |
| RULE-03 | ロスカット幅 **-8%以内** を強制 |
| RULE-04 | 推奨保有期間 **最大20営業日** |
| RULE-05 | 確信度スコア **50未満はHOLD強制** |
| RULE-06 | **ネガティブニュース検出時のBUY禁止** |
| RULE-07 | 同時保有 **最大4銘柄・総資産60%以内** |
| RULE-08 | **推測・憶測を根拠にした判断の禁止** |

---

## Setup

### 1. リポジトリのクローン

```bash
git clone https://github.com/YOUR_USERNAME/exa-investor.git
cd exa-investor
```

### 2. 仮想環境の作成と依存パッケージのインストール

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. 環境変数の設定

`.env.example` をコピーして `.env` を作成し、各APIキーを設定します。

```bash
cp .env.example .env
```

```env
# .env
GOOGLE_API_KEY=your_google_api_key_here
LINE_ACCESS_TOKEN=your_line_channel_access_token
LINE_USER_ID=your_line_user_id
```

- **GOOGLE_API_KEY**: [Google AI Studio](https://aistudio.google.com/) で取得（Gemini 2.5 Flash 使用）
- **LINE_ACCESS_TOKEN / LINE_USER_ID**: [LINE Developers](https://developers.line.biz/) でMessaging APIチャネルを作成

### 4. 決算資料のセットアップ（FundamentalAgent用）

分析したい企業の決算PDF（IR資料）を `unzipped_docs/` 配下に配置し、初回起動でChromaDBを構築します。

```bash
mkdir -p unzipped_docs/your_company_ir
# PDFファイルをunzipped_docs/配下にコピー
```

初回実行時に自動的にベクトルDBが `chroma_db_saved/` に保存されます（2回目以降は高速ロード）。

---

## Usage

```bash
# 通常実行（全エージェント起動 + LINE通知）
python main.py

# LINE通知なし（動作確認・開発用）
python main.py --no-line
```

### 監視銘柄・ティッカーの変更

`.agents/news_agent.yaml` と `.agents/technical_agent.yaml` を編集します。

```yaml
# .agents/technical_agent.yaml
params:
  tickers:
    エクサウィザーズ: "4840.T"
    三菱重工: "7011.T"
    # 任意の銘柄を追加可能
    トヨタ: "7203.T"
```

### BBS ログの確認

各実行のエージェント間通信ログは `bbs/YYYYMMDD_HHMMSS.json` に保存されます。

```bash
cat bbs/$(ls bbs/ | tail -1) | python -m json.tool
```

---

## Tech Stack

| カテゴリ | 技術 |
|---|---|
| LLM | Google Gemini 2.5 Flash (`langchain-google-genai`) |
| Embeddings | `intfloat/multilingual-e5-small` (`sentence-transformers`) |
| Vector DB | ChromaDB |
| RAG 手法 | Multi-HyDE（独自拡張） |
| 金融データ | yfinance（日足OHLCV） |
| ニュース | Google News RSS (`feedparser`) |
| 通知 | LINE Messaging API |
| エージェント設計 | ECC アーキテクチャ（スキル制限 + BBS共有メモリ） |

---

## Architecture Philosophy

このプロジェクトは **ECC (everything-claude-code)** のアーキテクチャ思想を金融ドメインに応用しています。

- **Skill Layer**: 各機能を副作用のない独立した `run()` 関数としてカプセル化
- **Agent Permission**: YAMLで許可スキルを明示的に列挙し、エージェントが越権できない設計
- **BBS Pattern**: エージェント同士が直接通信せず、共有テキストメモリを介して非同期に協調
- **Compliance Gate**: 最終意思決定の前に必ずルールベースの検閲を挟む多層防御

---

## License

MIT License

## 🔄 System Activity Log
- ⏱ **Backup & Run:** 2026-05-01 14:19:21

## 🔄 Development History
- 📅 **2026-05-01 14:28:10** | 🛠️ **内容:** `auto-backup: 2026-05-01 14:28:10 (定期バックアップ)` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/fadd468aca11b8174e413a31cff29f39e610f41e)
