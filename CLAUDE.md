# ai-investor-bot — プロジェクト概要

## プロジェクト概要

S&P500を対象にしたスイングトレード自律エージェントシステム。
5エージェント（TechnicalAgent / NewsAgent / MacroAgent / SocialAgent / FundamentalAgent）が協調し、ManagerAgentが最終売買判断を行う。

### 主要エントリポイント

- `python main.py --screen --dry-run` — S&P500スクリーニング＋ドライラン
- `python main.py --screen --notify-line` — スクリーニング＋LINE通知
- `python server_librarian.py` — 日報生成（`latest_summary.md`）
- `python server_librarian.py --ingest` — Wiki更新
- `python scripts/lint_wiki.py` — Wikiヘルスチェック

---

## 詳細仕様の索引

- **Knowledge Base Wiki（raw/wiki/logの3層スキーマ、Ingest処理ルール、lint-wikiチェック項目）**
  → `server_librarian.py --ingest` の実装・Wiki関連ファイル（`data/knowledge_base/`配下）を扱うときは
  `.claude/skills/knowledge-base-wiki/SKILL.md` を参照すること。
