---
name: agents-and-scoring
description: 6エージェント（Fundamental/Technical/Macro/News/Social/Liquidity）のウェイト・データソース・スコアリング仕様と、ManagerのSTRONG BUY発注ロジック（STRONG_BUY_SCORE、加重スコア、必須ゲート条件）。エージェントのウェイトや閾値（0.60/0.7/0.55等）について質問・変更するとき、engine/constants.py・agents/ 配下・skills/social_monitor.py・skills/liquidity_monitor.py を扱うとき、STRONG BUY判定やhype_score・買い煽りペナルティ・RAG自己修復（EDGAR）に関する作業をするときに参照する。
---

# Multi-Agent Scoring — エージェント構成と発注ロジック

## エージェント一覧とウェイト

```
┌──────────────────┬──────────┬──────────────────────────┬──────────────┐
│   エージェント   │ ウェイト │       データソース       │  スコア範囲  │
├──────────────────┼──────────┼──────────────────────────┼──────────────┤
│ FundamentalAgent │ 35%      │ ChromaDB / EDGAR 10-Q    │ -1.0 〜 +1.0 │
├──────────────────┼──────────┼──────────────────────────┼──────────────┤
│ TechnicalAgent   │ 20%      │ yfinance (OHLCV)         │ -1.0 〜 +1.0 │
├──────────────────┼──────────┼──────────────────────────┼──────────────┤
│ MacroAgent       │ 15%      │ yfinance (SPY/VIX)       │ -1.0 〜 +1.0 │
├──────────────────┼──────────┼──────────────────────────┼──────────────┤
│ NewsAgent        │ 10%      │ Finnhub News API         │ -1.0 〜 +1.0 │
├──────────────────┼──────────┼──────────────────────────┼──────────────┤
│ SocialAgent      │ 10%      │ Finnhub Social Sentiment │ -1.0 〜 +1.0 │
├──────────────────┼──────────┼──────────────────────────┼──────────────┤
│ LiquidityAgent   │ 10%      │ Futu OpenD (Moomoo)      │ -1.0 〜 +1.0 │
└──────────────────┴──────────┴──────────────────────────┴──────────────┘
```

> **[検証済み追記 — 定義場所]**（2026-07-08 コード確認）
> ウェイトの唯一の定義場所は `engine/constants.py:16-23` の `WEIGHTS` dict。
> コード内コメントに設計経緯あり:「LiquidityAgent 追加に伴い fundamental を
> -0.05, macro を -0.05 調整」（6要素の合計 = 1.00 を維持）。
> 離散trend→スコア変換は同ファイルの `SIGNAL_MAP`
> （positive=+1.0 / neutral=0.0 / negative=-1.0）。

## Stage-Gate設計 (コスト最適化)

```
Stage 1 (fast/cheap)         Stage 2 (expensive)
  TechnicalAgent               FundamentalAgent
  NewsAgent           Gate       (RAG + LLM)
  MacroAgent    ──►  Check ──►
                    ↓ ABORT
               Macro=-1 or
               (Tech=0 and News=0)
```

Stage 1のみで判断可能なケースではStage 2を実行しないことで、LLM/API呼び出し
コストを大幅に削減する。Gate条件の詳細は
`.claude/skills/architecture-pipeline/SKILL.md` を参照。
なお実装（`engine/agent_wrappers.py` の `_gate_check()`）は原文の `=0` ではなく
`<= 0.0` で比較しており、**意図的か実装バグか未確定の疑義がある**
（`docs/POTENTIAL_BUGS.md` #1）。修正・依存コード追加はユーザー承認必須。

## 各エージェントの詳細

### FundamentalAgent（RAG自己修復フロー）

```
ChromaDB検索 → ヒットなし → EDGARから10-Q自律取得
                            → PDFテキスト抽出
                            → チャンク化・ベクトル化
                            → ChromaDBへ保存
                            → 再検索 → LLM評価
```

> **[検証済み追記 — 実装場所とトリガー条件]**（2026-07-08 コード確認）
> - 自己修復の本体: `agents/fundamental_agent.py` の `_fetch_from_edgar()`
>   （Step 1c、428行目付近）。
> - トリガー: ChromaDB が関連チャンク0件を返し、かつ ticker が米国株らしい
>   （日本語文字を含まない）場合のみ。
> - 実処理: `skills/financial_data_loader.py` の
>   `FinancialDocumentLoader.fetch_from_edgar()` → `skills/edgar_fetcher.py`
>   （SEC EDGAR 公開REST API、APIキー不要、429レートリミット時は自動リトライ）。
> - 取得成功後は DB接続を無効化してから再検索し、新規チャンクを反映する
>   （680行目付近「再検索します...」）。ベクトル検索は `skills/rag_search.py`。

### SocialAgent（Finnhub API移行済み）

旧実装でStockTwitsスクレイピングが403エラーで弾かれる問題を解決。Finnhub
Social Sentiment APIに移行し、Reddit・Twitter/X等のSNSデータをLLM（Ollama）で
文脈解析してhype_score（0.0〜1.0）と定性的根拠を生成する。閾値
`SOCIAL_HYPE_THRESHOLD = 0.7` 以上を「過熱相場」と判定。

**設計理由（重要）**: StockTwits への回帰を提案しないこと。スクレイピングは
403 で恒常的にブロックされており、Finnhub 移行はその回避策として意図された
恒久対応である。

> **[検証済み追記 — 買い煽りペナルティの実装]**（2026-07-08 コード確認、
> `engine/agent_wrappers.py:624-641`）
> Manager集計時、SocialAgentのスコアには2つのモードがある：
> 1. **連続スコアモード**: BBSの `social_analysis` に `score` フィールドが
>    あればそれをそのまま使用（hype考慮済みの値。ペナルティ適用なし）。
> 2. **後方互換モード**（`score` なし）: sentiment を ±1.0/0.0 に変換。
>    ただし **買い煽りペナルティ** — `sentiment == "POSITIVE"` かつ
>    `hype_score >= SOCIAL_HYPE_THRESHOLD (0.7)` かつ
>    ファンダ・テクニカルの裏付けなし（`not (fa_sig > 0.0 and tech_sig >= 0.0)`）
>    の場合、social_sig は **-0.5 に反転**する。
>    「SNSだけが盛り上がっている銘柄はミーム株リスク」という設計意図。

### LiquidityAgent（Futu OpenD連携）

Futu OpenD（Moomoo）のリアルタイムAPIから大口純流入額・Bid/Ask比率を取得。
`bid_ratio > 0.55` → 買い圧力優勢、`net_large_inflow > 0` → 機関資金流入と判定。

> **[検証済み追記 — スコア計算式]**（2026-07-08 コード確認、
> `skills/liquidity_monitor.py`）
> 上記の 0.55 / 0 は「優勢」ラベル判定の閾値
> （`_DOMINANT_THRESHOLD = 0.55`）。Managerに渡るスコア自体は連続値：
>
> ```
> book_score = clamp((bid_ratio - 0.5) * 4,  -1.0, +1.0)
> flow_score = clamp(net_large_inflow / FLOW_SCALE, -1.0, +1.0)
> score      = 0.5 * book_score + 0.5 * flow_score
> ```
>
> `FLOW_SCALE = $5M`（`_FLOW_SCALE_USD = 5_000_000.0`、±1.0の飽和上限）。
> データ取得は `engine/fetchers/moomoo_fetcher.py`（OrderBook / CapitalFlow）。
> なお LiquidityAgent のスコアは Stage-Gate の Gate 判定には影響しない
> （Manager の加重統合にのみ寄与）。

## マネージャーの発注ロジック

```python
# engine/constants.py より
STRONG_BUY_SCORE = 0.60   # 加重スコアの Strong Buy 閾値

# 必須条件（ゲート条件）
FA_score  > 0.0    # Fundamental positive
Tech_score >= 0.0  # Technical not negative
News_score >= 0.0  # News not negative

# 総合スコア計算
weighted_score = Σ (agent_score × weight)  # 全6エージェント
```

> **[検証済み追記 — 実装の完全な判定式]**（2026-07-08 コード確認、
> `engine/agent_wrappers.py:660-668`）
>
> ```python
> macro_forced_hold = macro_sig < 0.0 and "macro" not in excluded_keys
>
> is_strong_buy = (
>     not macro_forced_hold          # ← 原文に無い第4の必須条件
>     and score    >= STRONG_BUY_SCORE
>     and fa_sig   >  0.0
>     and tech_sig >= 0.0
>     and news_sig >= 0.0
> )
> ```
>
> - **Macro が負なら加重スコアに関係なく強制HOLD**（Gateと二重のブレーキ。
>   アブレーション実験で macro を除外した場合のみ無効化される）。
> - 各シグナルは `_prefer_score()` により、BBSデータに連続値 `score`
>   フィールドがあればそれを優先し、なければ `SIGNAL_MAP[trend]` の離散値に
>   フォールバックする。
> - ウェイトは通常 `WEIGHTS` だが、AuditAgent によるサスペンション発動時は
>   再正規化された `effective_weights` が使われる
>   （`engine/trade_cycle.py:90` の `eff_weights`）。

> **[検証済み追記 — 混同注意: skills/signal_scorer.py]**（2026-07-08 確認）
> `skills/signal_scorer.py` には「決算期は FA×0.55」等の**動的ウェイト**の
> 実装が存在するが、**本番パイプラインからは参照されていない**
> （import しているのは `tests/test_signal_scorer.py` のみ）。
> 本番の発注判断は上記 `WEIGHTS` 固定＋サスペンション再正規化のみ。
> grep で signal_scorer のウェイトを見つけても本番仕様と混同しないこと。

## このskillの範囲で変更作業をする際の注意

- **ウェイト（`WEIGHTS`）と閾値（`STRONG_BUY_SCORE` / `SOCIAL_HYPE_THRESHOLD` /
  `_DOMINANT_THRESHOLD` 等）の変更は本番の売買判断に直結するため、
  必ずユーザー承認を得ること。** バックテスト（閾値0.60で勝率46.0%検証済み）
  との整合も崩れる。
- 共有定数の定義場所は `engine/constants.py` のみ。他ファイルへの定数の
  重複定義・ハードコードを追加しないこと（`_DOMINANT_THRESHOLD` /
  `_FLOW_SCALE_USD` のようなモジュール私有定数はそのモジュール内に留める）。
- `WEIGHTS` を変更する場合は6要素の合計が 1.00 になることを必ず確認する。
- `is_strong_buy` の条件式・比較演算子（`>` と `>=` の区別）を変えてはならない。
  `fa_sig > 0.0`（真に正）と `tech_sig >= 0.0`（非負）の違いは意図的。
- エージェントの追加・削除はウェイト再配分＋Gate・Manager双方の変更を伴う
  アーキテクチャ判断であり、最上位tier + ユーザー承認必須
  （`.claude/skills/model-tier-routing/SKILL.md` 参照）。
- 動作確認は `python main.py --screen --dry-run` で行い、`--notify-line` は
  ユーザーの指示なしに実行しない。
