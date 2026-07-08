# 実装バグ疑義リスト（draft）

**docs/DRIFT_CHECK.md との区別**:
- DRIFT_CHECK.md = 「ドキュメントが古い/不正確なだけ」の問題（実装が正）
- POTENTIAL_BUGS.md（本ファイル） = 「実装が設計意図と違う可能性がある」問題
  （どちらが正か未確定。**修正は必ずユーザー承認を得てから**）

| # | 箇所 | 設計意図（ドキュメント） | 実装の現状 | ステータス | 発見日 |
|---|---|---|---|---|---|
| 1 | Stage-Gate の ABORT 条件（`engine/agent_wrappers.py:102-118` `_gate_check()`） | `Macro = -1` or (`Tech = 0` and `News = 0`) — 等号判定 | `macro_sig < 0.0` or (`tech_sig <= 0.0` and `news_sig <= 0.0`) — 不等号判定。Tech/News が弱い負値（例 -0.1）でも Stage 2 がスキップされ、連続スコア導入後は挙動差が拡大しうる | ⚠️ 未確認の疑義。ユーザー確認によれば意図的な仕様変更ではなく実装バグの可能性が高い。本番売買ロジックのため修正はユーザー承認必須 | 2026-07-08 |
| 2 | `skills/training_data_collector.py:24-40` のローカル `_WEIGHTS` / `_INPUT_KEYS` | 本番 `WEIGHTS`（engine/constants.py、6エージェント: fundamental 0.35 / macro 0.15 / liquidity 0.10）と同期しているべき（コメント「main.py と同期して変更すること」） | 旧5エージェント構成のまま（fundamental 0.40 / macro 0.20、liquidity なし）。`_INPUT_KEYS` にも `liquidity_analysis` が欠落 | ⚠️ 同期漏れの可能性が高い。training_data.jsonl に記録される重み・入力が本番判断と食い違い、研究データ（AuditAgent勝率評価・将来のファインチューニング）の妥当性に影響しうる。修正はユーザー承認必須 | 2026-07-08 |
