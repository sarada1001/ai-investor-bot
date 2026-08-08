# 自律型金融AIエージェントシステム — アーキテクチャ詳細

> 作成日: 2026-05-02  
> 対象コード: `main.py`, `agents/fundamental_agent.py`, `skills/financial_data_loader.py`, `skills/training_data_collector.py`, `skills/edgar_fetcher.py`

---

## 1. システム概要

### 1.1 目的

本システムは、**米国株のスイングトレード（数日〜数週間保有）における自律的な売買判断**を行うマルチエージェントシステムである。

人間のアナリストが行う「ニュース確認 → テクニカル分析 → ファンダメンタルズ精査 → リスク計算 → 発注」という一連のプロセスを、5種類の専門エージェントが段階的に実行する。最終的な判断基準は「全エージェントシグナルの加重平均スコアが閾値 0.60 以上、かつファンダメンタルズ POSITIVE」という厳格な条件とし、False Positive（誤った買いシグナル）を最大限に排除する設計になっている。

### 1.2 動作モード

システムは3つのモードで動作する。

| モード | API呼び出し | EDGAR取得 | 発注 | 主な用途 |
|--------|------------|-----------|------|----------|
| **mock_mode** | スキップ（ダミーデータ） | スキップ | スキップ | フロー確認・トークン節約 |
| **hybrid_mode** | 実行（yfinance + Gemini） | 実行 | スキップ | 学習データ収集・本番前検証 |
| **通常モード** | 実行 | 実行 | 実行（Alpaca） | 本番運用 |

**ハイブリッドモード**は学習データの品質向上のために重要な位置づけであり、リアルな市場データを使って分析プロセスを走らせつつ、実際の発注だけをスキップする。これにより、`data/training/training_data.jsonl` に「本物の相場環境下でのエージェント思考プロセス」を継続的に蓄積できる。

---

## 2. エージェント構成

### 2.1 全体アーキテクチャ（ステージゲート方式）

```
[実行開始] python main.py --hybrid --ticker AAPL
       │
       ▼
┌─────────────────────────────────────────────────┐
│  Stage 1: 安価シグナルスキャン（並列的に4エージェント実行）  │
│  TechnicalAgent → BBS["technical_analysis"]      │
│  NewsAgent      → BBS["news_analysis"]           │
│  MacroAgent     → BBS["macro_analysis"]          │
│  SocialAgent    → BBS["social_analysis"]         │
└─────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────┐
│  Gate チェック（コスト制御）                          │
│  Macro NEGATIVE?  → ブレーキ発動 → HOLD（即終了）   │
│  Tech≤0 AND News≤0? → Fundamental スキップ → HOLD │
│  それ以外          → Stage 2 へ進む               │
└─────────────────────────────────────────────────┘
       │（Gate 通過時のみ）
       ▼
┌─────────────────────────────────────────────────┐
│  Stage 2: ファンダメンタルズ深層分析                   │
│  FundamentalAgent（RAG + EDGAR 自律取得）          │
│  → BBS["fundamental_analysis"]                  │
└─────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────┐
│  Stage 3: 最終評価                                │
│  ManagerAgent（BBS全エントリを読んで加重スコア算出）   │
│  → BBS["manager_judgment"]                      │
└─────────────────────────────────────────────────┘
       │（STRONG BUY 時のみ）
       ▼
┌─────────────────────────────────────────────────┐
│  Stage 4: ポジションサイジング                       │
│  RiskAgent（ATR × Fixed Fractional / Kelly）     │
│  → BBS["risk_analysis"]                         │
└─────────────────────────────────────────────────┘
       │
       ▼
[学習データ保存] training_data_collector.save_training_record()
→ data/training/training_data.jsonl に JSONL 追記
```

### 2.2 共有メモリ BBS（Bulletin Board System）

エージェント間通信は **BBS** と呼ぶテキストベースの共有メモリで行う。セッションごとに `bbs/{session_id}.json` を生成し、各エージェントは分析結果をキーと値のペアで書き込む。後続エージェントは `bbs.read(key)` でその値を読み出す。LLMへの直接API呼び出しによる密結合を避けており、エージェントの追加・交換が容易な設計になっている。

### 2.3 各エージェントの役割

#### TechnicalAgent（`main.py:TechnicalAgent`）
- `skills/technical_calc.py` の `analyze_ticker()` を呼び出す
- yfinance から6ヶ月の株価データを取得し、RSI(14)・MACD・SMA25 を計算
- シグナルを `positive / neutral / negative` に分類し、BBS に書き込む
- **ウェイト: 0.20**

#### NewsAgent（`main.py:NewsAgent`）
- yfinance の `ticker.news` から最新3件のニュースを取得
- Gemini-2.0-flash でタイトル・本文を読んでセンチメント判定
- **ウェイト: 0.10**

#### MacroAgent（`main.py:MacroAgent`）
- SPY（S&P500 ETF）と VIX を取得してマクロ環境を判定
- **特別権限**: NEGATIVE 判定が出た場合、他の全エージェントをスキップして即 HOLD を発動する「マクロブレーキ」として機能する
- **ウェイト: 0.20**

#### SocialAgent（`main.py:SocialAgent`）
- Reddit r/wallstreetbets 風のSNSセンチメントを分析（現在はモック実装、将来 Reddit API 連携予定）
- **Hypeスコア制御**: `hype_score ≥ 0.7` かつ FA・Tech の裏付けがない場合、ManagerAgent がシグナルを `+1.0` から `-0.5` にペナルティ補正する（「根拠なき買い煽り」フィルタ）
- **ウェイト: 0.10**

#### FundamentalAgent（`agents/fundamental_agent.py`）
- ChromaDB `financial_filings` コレクションに対して RAG（Retrieval-Augmented Generation）検索を実行し、一次情報（決算書・IR資料）に基づいた分析を行う
- データ不足時の自己修復フロー（後述 §4）を持つ
- **ウェイト: 0.40**（最も高いウェイト。ファンダメンタルが POSITIVE でなければ Strong Buy にならない）

#### ManagerAgent（`main.py:ManagerAgent`）
- BBS に書き込まれた全5エージェントのレポートを読み込み、加重スコアを算出
- **Strong Buy 条件**: スコア ≥ 0.60 **かつ** FA > 0 **かつ** Tech ≥ 0 **かつ** News ≥ 0 **かつ** Macro ≥ 0（AND条件）
- Gemini-2.0-flash で投資家向けの根拠テキスト（rationale）を200文字以内で生成

#### RiskAgent（`main.py:RiskAgent`）
- STRONG BUY 確定時のみ実行
- **Fixed Fractional方式**: 口座残高の2% ÷ (ATR×2) = 最大許容株数
- **Kelly Criterion（簡略版）**: 勝率55%・損益比1.5 で Kelly 分率を算出
- 両者の小さい方を `recommended_shares` として採用（保守的運用）

#### ExitAgent（`agents/exit_agent.py`）
- Selling Loop（Stage 0）で Buying Loop の前に毎日実行され、`data/portfolio.json` の全保有ポジションを評価する
- 判定は以下の順で行い、最初に成立したものを採用する:

| 順 | `exit_type` | 条件 |
|----|-------------|------|
| 1 | `PRICE_UNAVAILABLE` | 現在価格を取得できない → HOLD（安全側） |
| 2 | `TAKE_PROFIT` | 現在価格 ≥ ATR由来 `target_price`（未設定時のみ含み益 ≥ +10% のフォールバック） |
| 3 | `STOP_LOSS` | 現在価格 ≤ ATR由来 `stop_loss_price`（未設定時のみ含み損 ≤ -5% のフォールバック） |
| 4 | `MAX_HOLD` | 保有期間が `MAX_HOLD_DAYS` 営業日に到達（**TIME_EXIT**） |
| 5 | `THESIS_BROKEN` | 購入時の thesis が現在ニュースにより否定された（LLM 判定 → 失敗時はルールベースにフォールバック） |
| 6 | `CONTINUE` | 上記いずれも不成立 → HOLD |

- **`MAX_HOLD` を `THESIS_BROKEN` より前に置く理由**: `THESIS_BROKEN` は LLM 呼び出しを伴うため、時間切れが確定しているポジションで API を消費しないようにする
- **`MAX_HOLD_DAYS`（`engine/constants.py`）の現在値は `0` = TIME_EXIT 無効**。バックテスト（`run_backtest.py` / `run_agent_exam.py`）は 10 営業日前提でシミュレーションしているため、有効化するまでは本番とバックテストで Exit 戦略が異なる（→ `docs/DRIFT_CHECK.md` #19）。有効化前に `scripts/check_time_exit_impact.py`（読み取り専用）で影響を確認すること
- 経過営業日は `numpy.busday_count` で算出する。土日のみ除外し**米国市場の祝日は考慮しない**ため、TIME_EXIT が本来よりわずかに早く発火しうる（既知の制約）
- `entry_date` が空・パース不能・未来日の場合は WARNING を出して TIME_EXIT 判定をスキップし HOLD 側に倒す
- SELL 確定時は Alpaca 売り注文 → Obsidian ログ生成 → 購入ログの CLOSED 更新 → `training_data.jsonl` の WIN/LOSS 書き戻しを行う（注文失敗・スキップ時は SELL を HOLD に差し戻してポジションを保持する）

---

## 3. RAGパイプライン（Financial Filing Processing — FFP）

### 3.1 パイプライン概要

`skills/financial_data_loader.py` の `FinancialDocumentLoader` クラスが実装するパイプライン。FINSAGEアーキテクチャにインスパイアされた金融特化型 RAG 構成である。

```
[入力源 A] data/raw_documents/*.pdf  （手動配置: 日本語決算書など）
       │
       ▼ FinancialDocumentLoader.parse_pdf()
       │  pypdf（デフォルト）または MinerU（magic-pdf）でテキスト抽出
       │
       ▼ FinancialDocumentLoader.chunk_text()
       │  日本語対応セパレータ（句点 > 改行 > 読点 > スペース）で分割
       │  チャンクサイズ: 800文字（≈200-250トークン）, オーバーラップ: 80文字
       │
       ▼ FinancialDocumentLoader.ingest_to_chroma()
       │  intfloat/multilingual-e5-small でベクトル化
       │  ChromaDB（chroma_db_saved/）の financial_filings コレクションに格納
       │
[入力源 B] SEC EDGAR（US株: 10-Q / 10-K）  （自律的にダウンロード）
       │
       ▼ fetch_from_edgar()
       │  sec-edgar-downloader で最新フォームをダウンロード
       │  BeautifulSoup（lxml）でHTML/iXBRL → プレーンテキスト変換
       │  最大500,000文字に切り詰め
       │  チャンクに ticker メタデータを付与して ingest_to_chroma() へ
```

### 3.2 ChromaDB コレクション構造

| 項目 | 値 |
|------|-----|
| コレクション名 | `financial_filings` |
| 保存場所 | `chroma_db_saved/`（ローカルディスク永続化） |
| 埋め込みモデル | `intfloat/multilingual-e5-small`（日英バイリンガル対応） |
| チャンクサイズ | 800文字（≈200-250トークン） |
| オーバーラップ | 80文字 |
| メタデータ（EDGARチャンク） | `ticker`, `form` (10-Q/10-K), `source`, `filename`, `date` |
| メタデータ（PDFチャンク） | `source`, `filename`, `page`, `chunk_index` |

### 3.3 2段階検索（FundamentalAgent の retrieve_chunks）

FundamentalAgentは `retrieve_chunks()` で2段階の検索を行う：

**Phase 1 — メタデータフィルタ（高速・正確）**  
`db.get(where={"ticker": {"$eq": ticker}})` でEDGAR取得済みチャンクを直接ルックアップ。  
ヒットした場合はここで即座に返す（セマンティック検索を省略）。

**Phase 2 — セマンティック検索 + 関連性ガード**  
Phase 1 でヒットしなかった場合（PDFから取り込んだ等）にフォールバック。  
3種類の HyDE スタイルクエリ（売上成長・リスク・業績見通し）でコサイン類似度検索を実行し、  
「少なくとも1チャンクにティッカー文字列が含まれること」を確認する関連性ガードを通過したチャンクのみを返す。  
これにより、AAPLのクエリでエクサウィザーズのチャンクが混入するクロスカンパニー汚染を防ぐ。

---

## 4. 自己修復的検索フロー（Self-Healing RAG）

FundamentalAgent が実装する最も重要な機能である。**ChromaDBにデータがない米国銘柄に対して、自律的にEDGARから一次情報を取得し、RAG分析を可能にする**。

```
FundamentalAgent.analyze(ticker="AAPL")
       │
       ▼ ① retrieve_chunks("AAPL") 
       │   → ChromaDB 検索: 0チャンク取得
       │
       ▼ ② 日本語でないティッカー(US株) と判定
       │   → _fetch_from_edgar("AAPL") を自律起動
       │
       ▼ ③ FinancialDocumentLoader.fetch_from_edgar("AAPL", prefer_quarterly=True)
       │   → sec-edgar-downloader で 10-Q をダウンロード（失敗時は 10-K にフォールバック）
       │   → HTML → プレーンテキスト変換（BeautifulSoup + lxml）
       │   → chunk_text() でチャンク化（tickerメタデータ付き）
       │   → ingest_to_chroma() で financial_filings に格納
       │   → "chunks_added: N" を返す
       │
       ▼ ④ self._db = None（DBキャッシュを無効化して再接続を強制）
       │
       ▼ ⑤ retrieve_chunks("AAPL") を再実行
       │   → Phase 1: メタデータフィルタで N チャンク取得
       │
       ▼ ⑥ _analyze_with_rag(ticker, chunks)
           → チャンクをプロンプトの参考資料として注入
           → Gemini-2.0-flash で構造化JSON出力（revenue_growth, risks, outlook 等）
           → data_source = "RAG（一次情報：financial_filings）"
```

**フォールバック階層**:  
EDGAR取得も失敗した場合は `_analyze_with_yfinance()` が最終手段として実行される。yfinance の財務サマリー（時価総額・利益率・PER等）をプロンプトに渡してLLMが分析するが、`data_source = "yfinance（フォールバック）"` として学習データにラベルが付く。

---

## 5. 学習データ収集パイプライン

### 5.1 収集の仕組み

全ての実行サイクル（mock / hybrid / 通常）の終了直前に、`skills/training_data_collector.py` の `save_training_record()` が自動的に呼び出される（`main.py:1247` および `main.py:1129`）。

```python
# main.py 末尾（全モード共通）
record_id = _training_mod.save_training_record(
    session_id=session_id,
    ticker=ticker,
    bbs_entries=bbs.read_all(),  # 全エージェントの生出力
    judgment=judgment,           # ManagerAgent の Chain of Thought
    mock_mode=mock_mode,
    hybrid_mode=hybrid_mode,
)
```

### 5.2 JSONL レコード構造

`data/training/training_data.jsonl` の各行は1トレードサイクルに対応し、以下のフィールドを持つ：

```jsonc
{
  "record_id":   "uuid-v4",          // アウトカム紐付け用 ID
  "session_id":  "20260502_103015",
  "date":        "2026-05-02",
  "ticker":      "AAPL",
  "mock_mode":   false,
  "hybrid_mode": true,

  // ── 入力フィーチャー（各エージェントの生出力） ──
  "inputs": {
    "technical_analysis":    { "trend": "positive", "trend_reason": "...", ... },
    "news_analysis":         { "articles": [...], "avg_sentiment_score": 0.67 },
    "macro_analysis":        { "trend": "neutral",  "spy": {...}, "vix": {...} },
    "social_analysis":       { "sentiment": "POSITIVE", "hype_score": 0.4, ... },
    "fundamental_analysis":  { "trend": "positive", "data_source": "RAG...", ... }
  },

  // ── 教師信号（ManagerAgent の Chain of Thought） ──
  "manager_chain_of_thought": {
    "signals":        { "fundamental": 1.0, "technical": 1.0, "macro": 0.0, ... },
    "weights":        { "fundamental": 0.40, "technical": 0.20, ... },
    "weighted_score": 0.70,
    "threshold":      0.60,
    "macro_forced_hold": false,
    "social_hype_penalty": false,
    "strong_buy_conditions_check": { "score_above_threshold": true, ... },
    "rationale": "... 200文字以内の投資根拠テキスト ..."   // 自然言語推論の正解ラベル
  },

  // ── 出力ラベル ──
  "manager_output": {
    "decision":      "STRONG BUY",
    "score":         0.70,
    "is_strong_buy": true
  },

  // ── 取引結果（ExitAgent 実行後に update_outcome() で付与） ──
  "outcome":             null,     // 未決済
  "outcome_label":       null,     // "WIN" | "LOSS"（決済後に付与）
  "outcome_updated_at":  null
}
```

### 5.3 アウトカムラベルの付与

STRONG BUY が確定した場合、`open_positions_index.json` にポジション情報が登録される。ExitAgent が SELL を実行した際に `update_outcome(ticker, pnl_pct, exit_price, exit_reason)` を呼び出すことで、FIFO 方式で対応するレコードに `"WIN"` / `"LOSS"` ラベルが付与される（`agents/exit_agent.py` の `_record_exit()`）。`exit_reason` には ExitAgent の `exit_type`（`TAKE_PROFIT` / `STOP_LOSS` / `MAX_HOLD` / `THESIS_BROKEN`）がそのまま渡る。

---

## 6. 現在の実装状況（2026-05-02 時点）

### 完成している機能

| コンポーネント | 状態 | 備考 |
|--------------|------|------|
| ステージゲート方式の実行エンジン | ✅ 完成 | mock / hybrid / 通常の3モード |
| TechnicalAgent (RSI/MACD/SMA) | ✅ 完成 | yfinance + Gemini |
| NewsAgent（ニュースセンチメント）| ✅ 完成 | yfinance ニュース取得 |
| MacroAgent（SPY/VIX）| ✅ 完成 | マクロブレーキ機能含む |
| SocialAgent（Hypeスコア制御）| ✅ 完成（モック）| Reddit API統合は未着手 |
| FundamentalAgent (RAG + 2段階検索) | ✅ 完成 | |
| EDGAR 自律取得（自己修復フロー）| ✅ 完成 | 10-Q/10-K 自動ダウンロード |
| FFP パイプライン（PDF → ChromaDB）| ✅ 完成 | pypdf + MinerU対応 |
| ManagerAgent（加重スコア + Hypeペナルティ）| ✅ 完成 | |
| RiskAgent（Fixed Fractional + Kelly）| ✅ 完成 | |
| 学習データ収集（training_data.jsonl）| ✅ 完成 | |
| ExitAgent（Selling Loop）| ✅ 完成 | TAKE_PROFIT / STOP_LOSS / MAX_HOLD / THESIS_BROKEN。`MAX_HOLD_DAYS=0` のため TIME_EXIT は現在無効 |
| アウトカムラベル付与（WIN/LOSS）| ✅ 完成 | ExitAgent の `_record_exit()` から `update_outcome()` を呼ぶ |
| Reddit API 連携 | ❌ 未着手 | |
| ローカル LLM への蒸留 | ❌ 未着手（構想段階）| §7 参照 |

---

## 7. 今後の展望 — ローカルLLMへの蒸留（Distillation）

### 7.1 構想の概要

現在のシステムは全ての推論を **Gemini-2.0-flash**（クラウドLLM）に依存している。蒸留の目標は、蓄積した `training_data.jsonl` を教師データとして、**オフラインで動作するローカルLLM（例: Mistral-7B, LLaMA-3など）** に ManagerAgent 相当の意思決定能力を転移することである。

### 7.2 蒸留に向けたデータ設計の工夫

現在の `training_data.jsonl` は蒸留を意識した構造になっている：

- **入力**: 5エージェントの分析結果（数値シグナル + 自然言語理由）
- **中間思考（CoT）**: `manager_chain_of_thought` フィールド — 加重スコア算出の根拠・各条件チェックの結果・Hypeペナルティの適用判断
- **出力**: `decision`（STRONG BUY / HOLD）と `rationale`（200文字の投資根拠）
- **正解ラベル**: `outcome_label`（WIN / LOSS）— 実際の相場結果による事後評価

この構造は **Instruction Following + Chain of Thought Fine-tuning** に直接活用できる形式になっており、特に `rationale` フィールドは小型モデルに金融推論の「正解パターン」を教える教師信号として機能する。

### 7.3 蒸留の実施ステップ（計画）

```
① データ量確保（ハイブリッドモードで継続実行 → 数百〜数千レコード）
       │
       ② アウトカムラベルの付与（ExitAgent の決済 → WIN/LOSS で品質フィルタ）
       │
       ③ 学習データのクリーニング（mock_mode=false のみ使用、hybrid優先）
       │
       ④ ローカルLLMのファインチューニング
       │  手法: LoRA / QLoRA（低ランク適応）
       │  ベースモデル候補: Mistral-7B-Instruct, LLaMA-3-8B
       │  形式: Instruction → CoT → Answer
       │
       ⑤ 蒸留後モデルの評価
          精度: Gemini-2.0-flash との判断一致率
          収益性: バックテストでの損益率
          速度: クラウドAPIなしでの推論レイテンシ
```

### 7.4 目標とする最終形態

クラウドAPIへの依存をゼロにし、相場環境（VIX・ニュース・決算データ）を入力として受け取り、ローカルLLMがポートフォリオの売買判断を完全自律で実行するエッジ型AIシステムの実現を目指す。

---

## 8. ディレクトリ構成

```
exa-investor/
├── main.py                          # オーケストレーションエンジン（5エージェント + BBS）
├── agents/
│   └── fundamental_agent.py         # FundamentalAgent（RAG + EDGAR自己修復）
├── skills/
│   ├── financial_data_loader.py     # FFPパイプライン + fetch_from_edgar()
│   ├── edgar_fetcher.py             # EDGAR REST API クライアント（edgar_fetcherスキル）
│   ├── technical_calc.py            # RSI / MACD / SMA 計算
│   ├── news_monitor.py              # yfinanceニュース取得 + センチメント判定
│   ├── macro_monitor.py             # SPY / VIX 分析
│   ├── social_monitor.py            # SNSセンチメント（Hypeスコア）
│   ├── risk_calculator.py           # Fixed Fractional + Kelly ポジションサイジング
│   ├── alpaca_trade.py              # Alpaca Markets 発注
│   ├── rag_search.py                # ChromaDB汎用検索スキル
│   └── training_data_collector.py  # 学習データ収集（JSONL書き込み）
├── data/
│   ├── raw_documents/               # 手動配置PDFの置き場（日本語IR資料等）
│   └── training/
│       ├── training_data.jsonl      # 蒸留用学習データ（全サイクルを追記）
│       └── open_positions_index.json # WIN/LOSS未確定ポジションのインデックス
├── chroma_db_saved/                 # ChromaDB永続化ディレクトリ（financial_filings）
├── bbs/                             # セッションごとのBBSログ（JSON）
├── unzipped_docs/
│   └── edgar_dl/                   # EDGAR自動ダウンロードファイルの保存先
└── dashboard.py                    # 可視化ダッシュボード
```
