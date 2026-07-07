---
name: architecture-pipeline
description: システム全体のレイヤー構成、Stage-Gate Pipeline（trade_cycle）、BBS状態管理、DipScan急落スキャンの仕様。engine/runner.py・engine/trade_cycle.py・engine/bbs.py・engine/agent_wrappers.py を読む/修正するとき、Gate（ABORT）条件やパイプラインの流れを説明・変更するとき、bbs/ 配下のJSONを扱うとき、DipScan・急落エントリー・15分サブループに関する作業をするときに参照する。
---

# System Architecture — レイヤー構成と Stage-Gate Pipeline

## 全体レイヤー構成

```
┌──────────────────────────────────────────────────────┐
│  Cron / Daemon                  (エントリポイント)    │
│  python main.py --screen --notify-line               │
└────────────────────┬─────────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────────┐
│  engine/runner.py   Watchlist Cycle (100銘柄ループ)  │
│  ├─ DipScan subloop (15分間隔 / 急落エントリー)      │
│  └─ run_trade_cycle() per ticker                     │
└────────────────────┬─────────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────────┐
│  engine/trade_cycle.py   Stage-Gate Pipeline         │
│                                                      │
│  Stage 1 ──► Gate ──► Stage 2 ──► Risk ──► Manager  │
│  Tech/News/Macro    (abort if    Fundamental  ──►    │
│  (fast scan)         macro=-1    (RAG + EDGAR)       │
│                   or tech&news                       │
│                      both=0)                         │
│                                                      │
│  ──► CriticAgent ──► TradeGuard ──► LiveGate ──►    │
│       (LLM監査)      (ガードレール)  (本番認証)      │
│                                                      │
│  ──► AlpacaClient.submit_order()                     │
│  ──► ObsidianLogger → obsidian_logs/Log_*.md        │
│  ──► LINE通知                                        │
└──────────────────────────────────────────────────────┘
```

## Stage-Gate Pipeline と ABORT（Gate）条件

Stage 1（Tech / News / Macro の高速スキャン）の結果を Gate でチェックし、
条件を満たす場合は Stage 2（FundamentalAgent = RAG + LLM、高コスト）を
スキップして HOLD で即終了する。**目的はLLM/API呼び出しコストの削減**。

- ドキュメント上の ABORT 条件: `Macro = -1` or (`Tech = 0` and `News = 0`)
- Gate 通過後: Fundamental 分析 → Risk → Manager の加重統合 →
  CriticAgent（LLM監査）→ TradeGuard（ガードレール）→ LiveGate（本番認証）
  → Alpaca 発注 → ObsidianLogger → LINE通知

> **[検証済み追記 — 設計意図と実装の食い違い（⚠️ 未確認の疑義あり）]**
> （2026-07-08 コード確認）
>
> - **原文の設計意図**: `Macro = -1` or (`Tech = 0` and `News = 0`) で ABORT。
> - **実装の現状**（`engine/agent_wrappers.py:102-118` の `_gate_check()`）:
>
>   ```python
>   macro_brake  = macro_sig < 0.0                       # Macro が負ならブレーキ
>   signals_flat = tech_sig <= 0.0 and news_sig <= 0.0   # 両方「NEUTRAL 以下」で skip
>   skip         = macro_brake or signals_flat
>   ```
>
>   等号（`== 0`）ではなく不等号（`<= 0.0`）で判定している。
> - **ステータス: ⚠️ 未確認の疑義**。意図的な仕様変更か実装バグか未確定
>   （ユーザー確認によればバグの可能性が高い）。詳細は
>   `docs/POTENTIAL_BUGS.md` #1 を参照。**本番ロジックのため、この条件式の
>   修正は必ずユーザー承認を得てから行うこと。承認なしに「正しい姿」へ
>   直すことも、この実装を前提に新コードを書くことも避ける。**
>
> 以下は疑義とは独立した検証済み事実：
>
> - **MacroAgent がサスペンド中（`macro_data["suspended"] == True`、shadow mode）
>   の場合は `macro_sig = 0.0` に丸められ、ブレーキは発動しない**
>   （AuditAgent によるサスペンション時に市場全体を止めないための設計）。
> - Gate 判定を呼び出すのは `engine/trade_cycle.py`（`_gate_check(bbs, ticker)`、
>   300行目付近）。`gate["skip_fundamental"]` が True なら Stage 2 を実行しない。
> - CircuitBreaker 発動中も同ファイルで `gate_skipped: True` として
>   サイクル全体が停止する（180行目付近）。
> - LiquidityAgent のスコアは Gate 判定には**影響しない**（表示・参照のみ）。

## 状態管理: BBS方式

各エージェントは bbs/ ディレクトリ内のJSONファイルに分析結果を書き込む。
Managerはこれを集約して加重スコアを算出する。ファイルを介した疎結合設計により、
エージェントの追加・差し替えが容易。

概念モデル（各エージェントが書き込む6種の分析データ）:

```
bbs/
├── news_analysis.json         {"articles": [...], "avg_sentiment_score": 0.7}
├── technical_analysis.json    {"trend": "positive", "score": 0.82, ...}
├── macro_analysis.json        {"trend": "neutral", "vix": 17.3, ...}
├── fundamental_analysis.json  {"trend": "positive", "analyses": [...]}
├── social_analysis.json       {"hype_score": 0.6, "sentiment": "POSITIVE"}
└── liquidity_analysis.json    {"net_large_inflow": 5_500_000, "bid_ratio": 0.65}
```

> **[検証済み追記 — 実際のファイル構造]**（2026-07-08 コード・実データ確認）
> 上の6ファイル構成は**概念モデル**であり、実際のディスクレイアウトとは異なる。
> `news_analysis` 等の名前を持つ個別ファイルは存在しないので、探さないこと。
>
> 実装は `engine/bbs.py` の `BBS` クラス。実レイアウトは
> **セッション単位の1ファイル** `bbs/{session_id}.json`（例:
> `bbs/20260702_202325.json`、session_id はタイムスタンプ）で、構造は：
>
> ```json
> {
>   "session_id": "20260702_202325",
>   "created_at": "2026-07-02T20:23:25.123456",
>   "entries": [
>     {
>       "agent": "TechnicalAgent",
>       "key": "technical_analysis",
>       "timestamp": "...",
>       "data": {"trend": "positive", "score": 0.82}
>     }
>   ]
> }
> ```
>
> - 概念モデルの「6ファイル名」は、実際には各 entry の **`key` フィールド**
>   （`news_analysis` / `technical_analysis` / `macro_analysis` /
>   `fundamental_analysis` / `social_analysis` / `liquidity_analysis`）。
> - 読み出しは `bbs.read(key)` — entries を**末尾から**走査し、同じ key の
>   最新 entry の `data` を返す（`engine/bbs.py:42`）。
> - 書き込みは `bbs.write(agent_name, key, data)` — entries に追記して即保存。
>   dataclass は `to_dict()` があれば自動で dict 化される。
> - この方式のため、1セッションファイルが全エージェントの因果トレース
>   （誰が・いつ・何を書いたか）になる。研究上の監査ログも兼ねる設計。

## DipScan（急落エントリー機能）

engine/runner.py の `_run_dip_scan_subloop()` が、メインサイクルのスリープ中に
15分間隔でS&P500ユニバースをスキャンし、当日始値比 -3.0% 以上の急落銘柄を
LINEに通知する。通常サイクルとは独立して動作し、押し目エントリーの機会を
逃さない設計。

> **[検証済み追記 — 定数の定義場所]**（2026-07-08 コード確認）
> - スキャン間隔: `_DIP_SCAN_INTERVAL_SECS = 900`（`engine/runner.py:16`、
>   モジュールレベル定数）
> - 急落閾値: `dip_threshold_pct: float = -3.0`（`engine/runner.py:23`、
>   `_run_dip_scan_subloop()` の**関数デフォルト引数**であり
>   `engine/constants.py` には無い。閾値を変える場合は呼び出し側
>   （`engine/runner.py:234` 付近）か関数デフォルトを確認すること）

## このskillの範囲で変更作業をする際の注意

- **`bbs/` 配下は本番の実行時 state file。読み取りのみ可。**
  手動での編集・削除・整形は一切禁止（過去セッションの因果トレース＝
  研究データを破壊するため）。
- **パイプライン構造の変更（Stage/Gate の追加・削除・順序変更、BBSスキーマ変更）
  は最上位tierモデル + ユーザー承認が必須**（`.claude/skills/model-tier-routing/SKILL.md` 参照）。
- Gate 条件（`_gate_check()`）・CircuitBreaker 連携部は本番の売買判断に直結する。
  数値・比較演算子を1つ変えるだけで発注挙動が変わるため、変更前に必ず
  ユーザー承認を得ること。
- `engine/bbs.py` の `read()` は「同一 key の最新 entry」を返す仕様に依存する
  コードが多数ある。走査順（reversed）を変えてはならない。
- 変更後の動作確認は `python main.py --screen --dry-run`（発注なし）で行う。
  `--notify-line` 付きでの実行は本番通知が飛ぶため、ユーザーの指示なしに実行しない。
