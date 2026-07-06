---
name: knowledge-base-wiki
description: Obsidian互換の3層Knowledge Base Wiki（raw/wiki/log）のスキーマ定義とIngest処理ルール、lint-wikiヘルスチェック仕様。server_librarian.py --ingest の実装・修正時、data/knowledge_base/ 配下のファイル（obsidian_logs, wiki/INDEX.md, wiki/tickers/*.md, wiki/concepts/*.md, wiki/log.md）を読み書きするとき、または scripts/lint_wiki.py / lint-wiki コマンドを扱うときに参照する。
---

# Knowledge Base Wiki スキーマ（Second Brain）

Andrej Karpathy 氏が提唱する「AIによる個人ナレッジベース構築」手法を採用。
3層構造でログを生きたWikiに昇華させる。

## 層1: raw（生素材）

| ファイル/ディレクトリ | 説明 |
|---|---|
| `latest_summary.md` | 当日の自動生成日報（server_librarianが毎日上書き） |
| `data/knowledge_base/obsidian_logs/Log_YYYYMMDD_TICKER_ACTION.md` | 個別トレードログ（YAML frontmatter付き） |
| `bbs/*.json` | エージェント間通信ログ（BBSバス） |
| `data/screener/cache.json` | スクリーナー結果キャッシュ |

rawファイルは**絶対に編集しない**。WikiはIngest処理のみで更新する。

## 層2: wiki（生きたWiki）

パス: `data/knowledge_base/wiki/`

### INDEX.md（全体目次）

毎回のIngestで自動再生成する。以下を含むこと：

```markdown
# 投資ナレッジベース INDEX

## 現在の投資方針
（ManagerAgentの最新判断サマリー。ポートフォリオ状況と方針を記述）

## 保有ポジション
（portfolio.json から自動抽出）

## ティッカー別ページ
- [[tickers/AAPL]] — Apple Inc. | 最終評価: HOLD | 2026-05-06
- ...

## コンセプトページ
- [[concepts/macd_golden_cross]] — MACDゴールデンクロス
- ...

## 最近のIngest履歴
（log.md の最新5件）
```

### tickers/{TICKER}.md（銘柄別ページ）

各銘柄1ファイル。Ingestのたびに**追記・更新**する。フォーマット：

```markdown
---
ticker: AAPL
name: Apple Inc.
sector: Technology
last_updated: 2026-05-06
assessment: HOLD  # BUY / SELL / HOLD / WATCH
assessment_score: +0.42
---

# [[tickers/AAPL|AAPL]] — Apple Inc.

## 概要
（セクター・業種・時価総額などの基本情報）

## トレード履歴
| 日付 | アクション | 価格 | スコア | 結果 | ログ |
|---|---|---|---|---|---|
| 2026-05-06 | SELL | $284.18 | +0.80 | +5.84% | [[Log_20260506_AAPL_SELL]] |

## 評価変遷
（各Ingest時点でのassessmentスコアの推移を1行で追記）

## 関連コンセプト
- [[concepts/take_profit_strategy]] — 利確戦略
- [[concepts/sns_hype_trap]] — SNS買い煽りの罠

## 関連ニュース（最新3件）
（NewsAgentが取得したニュース見出しと感情スコア）
```

### concepts/{CONCEPT}.md（投資コンセプトページ）

エージェントの推論から抽出した投資知見を蓄積する。フォーマット：

```markdown
---
concept: macd_golden_cross
title: MACDゴールデンクロス
last_updated: 2026-05-06
linked_tickers: [AAPL, NKE]
---

# [[concepts/macd_golden_cross|MACDゴールデンクロス]]

## 定義
（コンセプトの説明）

## 観測事例
| 日付 | 銘柄 | 結果 | 備考 |
|---|---|---|---|
| 2026-05-05 | [[tickers/AAPL]] | 成功 (+5.84%) | RSI=30台から回復 |

## 教訓
（このコンセプトが有効/無効だった条件の帰納的まとめ）
```

**命名規則**: `{snake_case}.md`（例: `sns_hype_trap.md`, `stop_loss_rule.md`）

### log.md（Ingestログ）

1行1エントリ。**追記のみ**（絶対に既存行を削除しない）：

```
2026-05-06 13:00 | INGEST | AAPL,NKE,GEHC | tickers×3更新, INDEX再生成, concepts/take_profit_strategy更新
```

## 層3: 相互リンクルール

- 全Wikiページは `[[リンク]]` 形式でObsidian互換リンクを使用すること
- ティッカーページ → コンセプトページ: 観測されたパターンをリンク
- コンセプトページ → ティッカーページ: 事例としてリンク
- INDEX.md → 全ページ: 集約リンク
- Log_*.md への参照は `[[Log_YYYYMMDD_TICKER_ACTION]]` 形式（.md拡張子省略）

## Ingest処理ルール（server_librarian.py --ingest）

1. `data/knowledge_base/wiki/log.md` を読み込み、前回Ingest以降の新規ログを特定
2. 新規 `obsidian_logs/Log_*.md` を全件読み込む
3. 各ティッカーについて `tickers/{TICKER}.md` を更新
4. 新たなコンセプト（MACD、RSI急落など）を抽出し `concepts/` を更新
5. `INDEX.md` を再生成
6. `log.md` に1行追記

**LLM使用**: Gemini（利用可能時）→ Ollama（フォールバック）

## lint-wiki ヘルスチェックルール

`python scripts/lint_wiki.py` または `/lint-wiki` コマンドで実行。

チェック項目：
1. **リンク切れ検出**: `[[リンク]]` の参照先ファイルが存在するか
2. **孤児ページ検出**: どこからもリンクされていないページ
3. **矛盾検出**: 同一ティッカーの assessment が複数ページで食い違っていないか
4. **鮮度チェック**: last_updated が7日以上古いティッカーページを警告
5. **未リンクログ**: obsidian_logs 内のログがどのティッカーページからも参照されていないもの
