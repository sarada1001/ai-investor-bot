# ドキュメント・実装ドリフト箇所リスト（draft）

skill分割作業（2026-07-08）中に発見した「プロジェクト概要ドキュメント（Brush-up版）と
実装の食い違い」の記録。skill分割完了後、以下との突き合わせに使う：

- ブログ②（BBS因果トレース）
- BBS-研究接続図（docs/BBS_RESEARCH_CONNECTION.md）
- README §1-4

| # | ドキュメントの記述 | 実装の事実 | 影響範囲 | 発見日 |
|---|---|---|---|---|
| 1 | BBSは `bbs/` 配下の6つの固定名JSONファイル（news_analysis.json 等） | `engine/bbs.py` の BBS クラスがセッション単位ファイル `bbs/{session_id}.json`（entries配列）で管理。6つの名前は entry の `key` フィールド | §2-2、ブログ②、BBS-研究接続図、README | 2026-07-08 |
| 2 | Gate ABORT条件の等号/不等号の食い違い | → **docs/POTENTIAL_BUGS.md #1 に移動**（ドキュメント側でなく実装側がバグの可能性が高いと判明したため再分類）。なお「MacroAgentサスペンド中（shadow mode）はブレーキ無効」の仕様がドキュメント未記載である点はドリフトとしてここに残す（`engine/agent_wrappers.py:111-112`） | §2-1、アーキテクチャ図 | 2026-07-08 |
| 3 | DipScan閾値 -3.0% は（文脈上）定数として記載 | `engine/constants.py` ではなく `engine/runner.py:23` の関数デフォルト引数 `dip_threshold_pct: float = -3.0` | §8 | 2026-07-08 |
| 4 | 発注の必須条件は FA>0 / Tech>=0 / News>=0 の3つ | 実装には第4の条件 `not macro_forced_hold`（`macro_sig < 0.0` なら加重スコアに関係なく強制HOLD）がある（`engine/agent_wrappers.py:660-668`） | §3 発注ロジック、ブログ①（安全装置）にも関連 | 2026-07-08 |
| 5 | LiquidityAgent は「bid_ratio > 0.55 → 買い圧力優勢、net_large_inflow > 0 → 機関資金流入と判定」（離散判定風の記述） | 0.55/0 はラベル用閾値。Managerに渡るスコアは連続値 `score = 0.5*clamp((bid_ratio-0.5)*4) + 0.5*clamp(net_large_inflow/$5M)`（`skills/liquidity_monitor.py`） | §3 LiquidityAgent | 2026-07-08 |
| 6 | SocialAgent は「hype_score 0.7以上を過熱相場と判定」とのみ記述 | 実装は買い煽りペナルティ: POSITIVE + hype>=0.7 + ファンダ/テクニカル裏付けなし → social_sig を -0.5 に反転（`engine/agent_wrappers.py:624-641`）。連続スコアモード時はペナルティ不適用 | §3 SocialAgent、ブログ③（HOLD評価）にも関連 | 2026-07-08 |
| 7 | trade_guards.json のキーは `max_open_positions` / `max_position_ratio` | 実ファイルのキーは `max_positions` / `max_position_pct`（値 3/5/1.0 は一致） | §4 TradeGuard、ブログ①（安全装置） | 2026-07-08 |
| 8 | CircuitBreaker は「連続損失が閾値を超えた場合」に発動 | 実装は損失回数ではなくドローダウン率基準の3状態機械: SOFT_TRIP（日次 -5%、翌日自動リセット）/ HARD_TRIP（高値比 -10%、手動解除のみ）（`tools/circuit_breaker.py:31-32`） | §4 CircuitBreaker、ブログ①（安全装置） | 2026-07-08 |
| 9 | CriticAgent は「LLM（Ollama）でリスクチェック、リスク検出で発注ブロック」 | 実装は Reflexion フェーズ2: 過去の失敗ルールとのRAG照合で APPROVE/OVERRIDE 判定。バックエンドは Ollama→Gemini→フェイルセーフHOLD の3段フォールバック（`tools/critic_agent.py`） | §4 CriticAgent、ブログ①② | 2026-07-08 |
| 10 | LiveTradingGate の説明に旧・手動認証の言及なし | 旧方式（`--enable-live` + `data/live_trading_enabled.json`、24h期限）は後方互換で残存するが `check()` では未使用（`tools/live_trading_gate.py`） | §4 LiveTradingGate | 2026-07-08 |
| 11 | 評価スクリプト表に `evaluate_financebench.py` が記載 | 実在しない。クリーンアップコミット `c7f4ada` で削除済み（`git show c7f4ada^:evaluate_financebench.py` で復元可能） | §7 評価スクリプト表、Phase B ロードマップ | 2026-07-08 |
| 12 | `neutral_zone_backtest.py` の目的は「スコア中立ゾーン（0.40〜0.60）の分析」 | 実際は MACD / SMA25 の「不感帯（neutral zone）」有無のA/B比較バックテスト（同ファイル docstring）。スコア0.40〜0.60帯の分析スクリプトではない | §7 評価スクリプト表 | 2026-07-08 |
| 13 | 「中立ゾーン 0.40〜0.60」が定義済みの概念として記載 | 単一の定数ペアはコードに存在しない。下限0.40 = `scripts/run_backtest.py:62` の `BUY_SCORE_THRESHOLD`、上限0.60 = `engine/constants.py:6` の `STRONG_BUY_SCORE` の組み合わせで成立する研究上の概念 | §7、ブログ③（HOLD評価） | 2026-07-08 |
| 14 | LINE通知フォーマットに「根拠サマリー（60文字）」を含むと記載 | `engine/notify.py` の `send_line_notification()` 本文には理由文字列が含まれない。`[:60]` 切り詰めは実在するがターミナル/ログ出力側（`engine/runner.py`, `engine/trade_cycle.py:632`, `engine/agent_wrappers.py:256`）の話で、LINE本文とは別 | §10 LINE通知フォーマット、ブログ①② | 2026-07-08 |
| 15 | Cron運用フローに `git pull` の記載なし | 実際の `run_bot.sh` は `python main.py` 実行前に `git pull origin main` を自動実行している。Phase Aロードマップの「自動デプロイ（CD / git pull hook）」は部分的に実装済み | §10 Cron運用フロー、§12 Phase A | 2026-07-08 |
| 16 | Wikiエンジンの LLM は「Gemini/Ollama」とのみ表記（バージョン不明瞭） | `server_librarian.py` の `call_gemini()` docstring は明示的に **Gemini 2.0 Flash** と記載（表の書きぶりでは2.5系を想起させる可能性あり） | §5 コアスタック表 | 2026-07-08 |
| 17 | 開発環境は「Python 3.11 (venv) / Ubuntu 22.04 LTS」と断定的に記載 | skill作成時にコマンドを実行したサンドボックス環境は `Python 3.14.4` / `Ubuntu 26.04 LTS` だったが、これがユーザーの物理ThinkPad開発機と同一環境かは未確認。要ユーザー確認（断定不可） | §1 開発環境 | 2026-07-08 |
| 18 | 旧 CLAUDE.md 冒頭サマリーが「5エージェント（Technical/News/Macro/Social/Fundamental）」と記載 | 実際は6エージェント構成（LiquidityAgent追加済み、ウェイト10%）。`engine/constants.py` の `WEIGHTS` に6キー存在、コメントに追加経緯あり | CLAUDE.md、README | 2026-07-08 |
| 19 | ~~バックテスト成績（勝率46.0% 等）が本番戦略の成績として記載~~ | ~~バックテスト（`run_backtest.py` の `MAX_HOLD_DAYS=10` / exit_reason `MAX_HOLD`、`run_agent_exam.py` の同 10 営業日 / `TIMEOUT`）は最大保有日数を前提にシミュレーションしていたが、本番 `agents/exit_agent.py` の `_evaluate()` には時間軸 Exit が存在せず、ストップロス／利確／thesis破綻に触れない限り無期限保有だった。つまり本番とバックテストは別戦略~~ → **✅ 解消済み（2026-08-08）** | §7 評価スクリプト表、バックテスト成績の解釈全般、ブログ③ | 2026-08-08 |

## 解消済み項目の詳細

### #19 本番 ExitAgent の時間軸 Exit 欠落（2026-08-08 解消）

**対応内容:**

- `agents/exit_agent.py` の `_evaluate()` に `MAX_HOLD` 分岐（TIME_EXIT）を追加。
  判定順序は `PRICE_UNAVAILABLE → TAKE_PROFIT → STOP_LOSS → MAX_HOLD → THESIS_BROKEN → HOLD`。
  LLM を呼ぶ `THESIS_BROKEN` より前に置き、時間切れ確定ポジションで API を消費しない。
- 最大保有日数は `engine/constants.py` の `MAX_HOLD_DAYS` に集約。
  **初期値は 0（無効）** であり、有効化は人間の別判断・別コミットで行う。
  つまり本項目は「実装の乖離」としては解消済みだが、
  **本番挙動が実際にバックテストと一致するのは `MAX_HOLD_DAYS` を 10 にした時点から**。
- バックテスト側（`run_backtest.py` / `run_agent_exam.py`）のハードコード `MAX_HOLD_DAYS = 10` は
  `engine/constants.py` の `BACKTEST_MAX_HOLD_DAYS` を既定値とする CLI 引数 `--max-hold-days` に置換。
- `run_agent_exam.py` の exit_reason `"TIMEOUT"` を `"MAX_HOLD"` に統一（3箇所で同じ命名になった）。
- 有効化前の影響確認用に `scripts/check_time_exit_impact.py`（読み取り専用）を追加。

**既知の制約:** 経過営業日は `numpy.busday_count` で算出するため土日のみ除外し、
米国市場の祝日は営業日としてカウントされる。実際の営業日数よりわずかに多く数えられ、
TIME_EXIT が本来より少し早く発火しうる（保有を引き延ばさない方向の誤差）。
祝日カレンダー用の新規依存パッケージは追加していない。
