# ai-investor-bot — プロジェクト概要

## 絶対制約（tierを問わず厳守）

以下は**いかなる状況でも変更不可**。変更が必要に見える場合も必ずユーザーの明示的な承認を得ること：

- 本番トレーディングロジック（`engine/`, `agents/`, `skills/` の売買判断コード）
- Safety機構の実装コード（TradeGuard / CircuitBreaker / LiveTradingGate / AuditAgent / CriticAgent）
- State files（`bbs/`, `data/portfolio.json`, `data/trade_guard_state.json`, `data/agent_status.json` など）
- Credentials（`.env`, APIキー）

**最上位tierモデル以外（Sonnet等）で作業する場合は、まず
`.claude/skills/model-tier-routing/SKILL.md` を読むこと。**

## プロジェクト概要

S&P500を対象にしたスイングトレード自律エージェントシステム。
6エージェント（TechnicalAgent / NewsAgent / MacroAgent / SocialAgent / FundamentalAgent / LiquidityAgent）が協調し、ManagerAgentが最終売買判断を行う。

### 主要エントリポイント

- `python main.py --screen --dry-run` — S&P500スクリーニング＋ドライラン
- `python main.py --screen --notify-line` — スクリーニング＋LINE通知
- `python server_librarian.py` — 日報生成（`latest_summary.md`）
- `python server_librarian.py --ingest` — Wiki更新
- `python scripts/lint_wiki.py` — Wikiヘルスチェック

---

## 詳細仕様の索引（タスク種別 → 読むべきskill）

| タスク種別 | 参照先 |
|---|---|
| どのモデルで作業するか判断、フォールバック手順 | `.claude/skills/model-tier-routing/SKILL.md` |
| システム全体構成、Stage-Gate、BBS状態管理、DipScan | `.claude/skills/architecture-pipeline/SKILL.md` |
| エージェントのウェイト・閾値、STRONG BUY発注ロジック | `.claude/skills/agents-and-scoring/SKILL.md` |
| TradeGuard/CircuitBreaker/LiveTradingGate/AuditAgent/CriticAgent | `.claude/skills/safety-guardrails/SKILL.md` |
| バックテスト・評価スクリプト・ユニバース・研究データ | `.claude/skills/evaluation-research/SKILL.md` |
| Cron/Daemon運用、LINE通知、コアスタック、開発環境 | `.claude/skills/infra-ops/SKILL.md` |
| Knowledge Base Wiki（raw/wiki/log、Ingest、lint-wiki） | `.claude/skills/knowledge-base-wiki/SKILL.md` |
| 今の進捗・TODO・ロードマップ（変動情報） | `STATUS.md` |

**タスクに複数のskillが関わる場合は、案内された全てを読むこと。**
各skillの「変更時の注意」セクションは、そのskillの範囲での不可侵事項・承認要件を定める。
