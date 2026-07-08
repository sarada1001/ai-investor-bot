# STATUS — 現在の達成状況とロードマップ

> **これは変動情報です。** 安定した仕様（アーキテクチャ・エージェント設計・
> セーフティ機構・評価フレームワーク・インフラ）は `.claude/skills/` 配下の
> 各SKILL.mdを参照してください。本ファイルは「今どこまで進んでいて、
> 次に何をやるか」だけを記録します。
>
> **完了判定のチェック手順（再発防止）**: このファイルで項目を✅完了と
> 書く前に、行数やファイルの存在確認だけで判断しないこと。必ず中身を
> 実際に読み、`<!-- TODO -->` 等の内部マーカー・冒頭の状態宣言
> （「翻訳未了」「draft」等）の有無を確認してから完了と書く。
> （2026-07-08: README.en.mdを行数のみで✅完了と誤判定した反省より）
>
> **最終更新**: 2026-07-08（skill分割Phase 2作業時に実ファイルと突き合わせて更新。
> README.en.mdの完了判定を訂正）

## 現在の達成状況

| 機能 | ステータス |
|---|---|
| RAG自己修復フロー（EDGAR自律取得） | ✅ 稼働中 |
| Futu API連携（リアルタイム流動性） | ✅ 稼働中 |
| Alpaca実弾発注 / ストップロス管理 | ✅ 稼働中 |
| SocialAgent Finnhub API移行 | ✅ 完了 |
| DipScanサブループ | ✅ 稼働中 |
| AuditAgent / サスペンション機能 | ✅ 実装済み |
| ObsidianLogger / Wiki自動更新 | ✅ 稼働中 |
| バックテスト / アブレーション評価基盤 | ✅ 実装済み |
| Streamlit ダッシュボード（dashboard/app.py） | ✅ 実装済み（650行、queries.py分離済み） |
| GitHub Actions CI（テスト自動実行） | ✅ 実装済み（`.github/workflows/ci.yml`、push/PR/毎朝ナイトリー実行） |
| CD（自動デプロイ） | 🔧 部分実装 — `run_bot.sh` が実行前に `git pull origin main` を行う簡易版のみ。GitHub Actions側のCDジョブは未実装（詳細: `docs/CICD.md`） |

> **[更新点]** 前回把握していた進捗（貼付ドキュメント時点）から以下が
> 新たに完了・判明しています:
> - Streamlit ダッシュボードは「🔧 継続改善中」から**実装済み**に進捗
>   （`dashboard/app.py` 650行 + `dashboard/queries.py`）。稼働確認・磨き込みは
>   継続中の可能性があるため、UI経由の動作確認は別途必要。
> - GitHub Actions CI は「📋 計画中」から**実装済み**に進捗
>   （`.github/workflows/ci.yml`）。ただしCDは未実装、詳細は `docs/CICD.md`。
> - **重要な安全側の事実**: `docs/SAFETY.md` の記述によれば、
>   LiveTradingGate と TradeGuard は 2026-06-03 のコミット `d6f7bd1`
>   （「TradeGuardの緩和とLiveTradingGateの完全自動化」）で**意図的に
>   自動化・緩和**されており、旧README等に残っていた「24時間手動同意」
>   「1銘柄20%上限」という記述は現在の挙動と一致しない
>   （現在は `max_position_pct: 1.0` = 実質上限なし、本番認証も
>   `.env` 設定のみで自動判定。詳細は
>   `.claude/skills/safety-guardrails/SKILL.md` および `docs/SAFETY.md`）。

## やるべきことリストの進捗

貼付ドキュメント時点のTODOリストと現状を突き合わせた結果：

| 項目 | ステータス | 実体 |
|---|---|---|
| 英語README | 🔧 部分完了 | `README.en.md`（422行）。**訂正**: 行数のみで完了と誤判定していた。ファイル自身の冒頭（6-9行目）に「Sections 1–4 are fully translated. Sections 5–16 are condensed — see the inline `<!-- TODO -->` markers」と明記されている。実際に `<!-- TODO -->` マーカーが付くのは §5 Day-to-Day Operations / §6 monitor.py / §7 dashboard.py / §8 Enabling Live Trading（安全に関わるため要約禁止と自己注記） / §9 Setup / §10 Command Reference / §11 Knowledge Base-Wiki / §14 Directory Structure / §15 Engineering Highlights の9セクション（うち§8,9,10,15はREADME.md日本語版へのリンクで代替）。TODOマーカーなしで実文が入っているのは §1-4（完全翻訳）と §12 Infrastructure / §13 Tech Stack / §16 Roadmap のみ |
| アーキテクチャ図 | ✅ 完了 | `docs/diagrams/architecture.mmd` |
| セキュリティ・リスク管理設計の説明 | ✅ 完了 | `docs/SAFETY.md`（4層防御、2026-06-03緩和の経緯も明記） |
| テストの内訳 | ✅ 完了 | `docs/TEST_REPORT.md`（`scripts/gen_test_report.py` 自動生成。**334件**中332 PASSED / 2 FAILED-ERROR、成功率99.4% — 元TODOの「332件」は総数ではなくPASSED数だった可能性） |
| CI/CDの説明 | ✅ 完了 | `docs/CICD.md`（CIのみ実装、CD未実装と明記） |
| BBSと研究の接続図 | ✅ 完了 | `docs/BBS_RESEARCH_CONNECTION.md` + `docs/diagrams/bbs_research_connection.mmd` |
| デモ動画 | ⬜ 未着手 | — |
| 技術ブログ3本 | ✅ 全3本完成 | 下記参照 |

**残タスクは「デモ動画」＋「README.en.md §5-16の完全翻訳（9セクション、うち§8は安全に関わるため要約せず逐語翻訳が必要）」の2件**です。

## 技術ブログ3本（全て完成済み）

| # | タイトル | ファイル | 行数 |
|---|---|---|---|
| ① | 「LLMトレードボットに四層安全装置を実装した話」 | `docs/blog/01_four_layer_safety.md` | 87行 |
| ② | 「マルチエージェントAIの判断をBBSで因果トレースする」 | `docs/blog/02_bbs_causal_tracing.md` | 103行 |
| ③ | 「AIのBUYではなくHOLDを評価する研究設計」 | `docs/blog/03_hold_evaluation_research_design.md` | 111行 |

> ①③の執筆・更新時は `docs/DRIFT_CHECK.md` と `docs/POTENTIAL_BUGS.md`
> （skill分割Phase 2で作成、draft）を突き合わせに使うこと。特に①は
> CircuitBreakerの発動基準（連続損失回数ではなくドローダウン率）、
> ③は「中立ゾーン0.40〜0.60」の実体（単一定数ではなく2箇所の定数の組み合わせ）
> に関する食い違いが見つかっている。

## 今後のロードマップ

### Phase A: システム安定化

- [x] GitHub Actions による自動テスト（CI）
- [~] uema2lab-search への自動デプロイ（CD / git pull hook）— `run_bot.sh` の
      `git pull origin main` が簡易版として機能中。GitHub Actions側のCD
      ジョブ化は未着手
- [ ] `scripts/preflight_check.py` のCI組み込み（同スクリプトの存在は
      本skill作成時に未確認。着手前に実在確認が必要）

### Phase B: 研究・評価強化（論文向け）

- [ ] アブレーション実験の系統的実施・結果集計
- [ ] FinanceBenchによるRAG精度ベンチマーク（市販システムとの比較）—
      `evaluate_financebench.py` は削除済み
      （`.claude/skills/evaluation-research/SKILL.md` 参照、
      `git show c7f4ada^:evaluate_financebench.py` で旧実装を復元可能）
- [ ] Faithfulnessスコアのトレンド分析
- [ ] 介入実験（CriticAgentあり vs なし）の有意差検定

### Phase C: UI / 可視化

- [~] Streamlit ダッシュボードの完成（`dashboard/app.py` 実装済み、
      リアルタイムBBS状態・ポートフォリオP&L表示の網羅性は未検証）
- [ ] Wiki（Obsidian）との双方向連携

### Phase D: 学習ループ

- [ ] `data/training/training_data.jsonl` を用いたLLMファインチューニング
- [ ] エージェントウェイトのオンライン最適化（強化学習）

> ⚠️ Phase D着手前に `docs/POTENTIAL_BUGS.md` #2
> （`skills/training_data_collector.py` のローカル `_WEIGHTS` が本番と
> 非同期）を解消しておくこと。学習データの重み記録が本番と食い違ったまま
> ファインチューニングに進むと、収集データの妥当性に疑義が残る。

## ポートフォリオ関連の残タスク（原文の「いずれやりたいことリスト」より）

- デモ動画（未着手）
- README.en.md §5-16 の完全翻訳（9セクション、`<!-- TODO -->` マーカー箇所。
  §8 Enabling Live Trading は安全に関わるため要約せず逐語翻訳が必要と
  ファイル自身が注記している）

---

## Draft状態の内部メモ（コミット判断待ち）

skill分割Phase 2作業中に作成した以下の2ファイルは **draft** であり、
このコミットには含めない：

- `docs/DRIFT_CHECK.md` — ドキュメントと実装の食い違い（実装が正）を記録
- `docs/POTENTIAL_BUGS.md` — 実装が設計意図と違う可能性がある箇所を記録
  （ステータス⚠️、修正はユーザー承認必須）

両ファイルとも「skill分割完了後、ブログ②・BBS-研究接続図・README §1-4との
突き合わせに使う」目的で蓄積中。コミットタイミングはユーザー判断待ち。
