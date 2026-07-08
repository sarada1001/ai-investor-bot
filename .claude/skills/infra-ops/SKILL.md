---
name: infra-ops
description: コアスタック（LLM/RAG/発注/データソース/通知/ログ）、Cron・Daemon運用フロー、LINE通知フォーマット、開発環境の仕様。run_bot.sh・crontab・requirements.txt・engine/notify.py を扱うとき、本番/ローカル環境の差異を確認するとき、Daemonモード（--daemon）・DipScanサブループの運用について作業するとき、LINE通知メッセージのフォーマットを変更するときに参照する。
---

# Infra & Ops — コアスタックと運用フロー

## コアスタック

```
┌─────────────────┬─────────────────────────────────────────────────────┐
│    レイヤー     │                    技術スタック                     │
├─────────────────┼─────────────────────────────────────────────────────┤
│ LLMエンジン     │ Ollama（ローカルLLM、自律思考・テキスト生成）       │
├─────────────────┼─────────────────────────────────────────────────────┤
│ RAGパイプライン │ ChromaDB（ベクトルDB）+ LangChain                   │
├─────────────────┼─────────────────────────────────────────────────────┤
│ 発注・資産管理  │ Alpaca API（Paper / Live切り替え対応）              │
├─────────────────┼─────────────────────────────────────────────────────┤
│ 流動性データ    │ Futu OpenD (Moomoo)                                 │
├─────────────────┼─────────────────────────────────────────────────────┤
│ SNSデータ       │ Finnhub Social Sentiment API                        │
├─────────────────┼─────────────────────────────────────────────────────┤
│ ニュースデータ  │ Finnhub News API                                    │
├─────────────────┼─────────────────────────────────────────────────────┤
│ SECデータ       │ EDGAR（10-Q自律取得）                               │
├─────────────────┼─────────────────────────────────────────────────────┤
│ 株価データ      │ yfinance（OHLCV・テクニカル計算用）                 │
├─────────────────┼─────────────────────────────────────────────────────┤
│ 通知            │ LINE Messaging API                                  │
├─────────────────┼─────────────────────────────────────────────────────┤
│ ログ管理        │ ObsidianLogger → data/knowledge_base/obsidian_logs/ │
├─────────────────┼─────────────────────────────────────────────────────┤
│ Wikiエンジン    │ server_librarian.py（Gemini/Ollama）                │
└─────────────────┴─────────────────────────────────────────────────────┘
```

> **[検証済み追記 — 実装の裏付けと訂正]**（2026-07-08 コード・requirements.txt確認）
> - LLM/RAG系ライブラリは `requirements.txt` に実在: `langchain==1.2.12` /
>   `langchain-google-genai==4.2.1`（Gemini 2.5 Flash 用）/
>   `langchain-huggingface==1.2.1` / `chromadb==1.5.5` /
>   `sentence-transformers==5.5.0`（multilingual-e5-small を自動DL）。
> - `server_librarian.py` は表記どおり Gemini/Ollama 両対応
>   （`call_gemini()` = Gemini 2.0 Flash 経由、`call_ollama()` = ローカル
>   Ollama、接続失敗時は `[ERROR]` 文字列を返すフェイルセーフ）。
>   ※表の「Wikiエンジン」欄では Gemini 2.5 Flash 相当と読めるが、実装の
>   `call_gemini()` docstring は **Gemini 2.0 Flash** と明記。バージョン
>   表記の食い違いは `docs/DRIFT_CHECK.md` 参照。
> - 通知: `requests==2.32.5` を使い `engine/notify.py` が LINE Messaging API
>   を呼ぶ（後述）。
> - RAG本体・EDGAR取得の詳細は `.claude/skills/agents-and-scoring/SKILL.md`
>   の FundamentalAgent 節を参照（本skillでは重複記載しない）。

## Cron運用フロー

```
[Cron 平日 23:00 JST]
  run_bot.sh
    └─ python main.py --screen --notify-line
         ├─ S&P500 100銘柄スクリーニング
         ├─ 全銘柄に対しrun_watchlist_cycle()
         ├─ STRONG BUY → Alpaca実弾発注
         └─ LINE通知（スコア・根拠・注文ステータス）
```

> **[検証済み追記 — run_bot.sh の実内容]**（2026-07-08 リポジトリ内
> `run_bot.sh` 確認）
>
> ```bash
> #!/bin/bash
> export PATH=/usr/local/bin:/usr/bin:/bin
> cd /home/naito/ai-investor-bot || exit 1
> git pull origin main
> /home/naito/ai-investor-bot/venv/bin/python3 main.py --screen --notify-line
> ```
>
> - 本番デプロイパスは `/home/naito/ai-investor-bot`
>   （本番ホスト名 `uema2lab-search`。Live Migration Standby メモリと整合）。
> - 実行前に **`git pull origin main` を自動実行**（原文に記載なし）。
>   これがそのまま「継続的デプロイ（CD / git pull hook）」の実体であり、
>   Phase A ロードマップの「自動デプロイ」は**既に部分的に実装済み**。
> - venv の Python バイナリを絶対パスで直接呼んでおり、activate は経由しない。

## Daemonモード

```
[Daemon mode: --daemon]
  1時間間隔ループ（DAEMON_INTERVAL_SECS = 3600）
  + DipScan 15分サブループ
```

> **[検証済み追記]**（2026-07-08 コード確認）
> - CLIフラグは `--daemon`（別名 `--auto`、`main.py` の argparse で
>   `dest="daemon"` に統合。どちらを指定しても同じ）。
> - `DAEMON_INTERVAL_SECS = 3_600` の定義場所は `engine/constants.py:9`。
> - DipScanサブループの詳細（`_DIP_SCAN_INTERVAL_SECS = 900`、閾値-3.0%）は
>   `.claude/skills/architecture-pipeline/SKILL.md` を参照（重複記載しない）。

## LINE通知フォーマット

- 銘柄・決定・加重スコア
- 根拠サマリー（60文字）
- Alpaca注文ステータス（約定価格・数量）

> **[検証済み追記 — 実際のメッセージ本文と原文の齟齬]**（2026-07-08
> `engine/notify.py` の `send_line_notification()` 確認）
> 実装は `send_line_message()` / `send_line_notification()` の2関数
> （`engine/notify.py`）。実際の LINE 本文構成は：
>
> ```
> 【ECC スクリーニング結果】{date}
> {N}銘柄を分析 | 通過: {M}銘柄
>
> 🚀 AAPL  STRONG BUY  スコア +0.7234
>    ✅ Alpaca発注済: ×1株  ID=xxxxxxxx
> 📈 MSFT  BUY  スコア +0.4521
>    🔵 Dry-Run（発注スキップ）
>
> ⏸ HOLD (12銘柄): NVDA, GOOGL, ...
> ```
>
> - **⚠️ 根拠サマリー（60文字）は LINE 本文には含まれない。**
>   `[:60]` での理由文字列の切り詰めは実在するが、それは**ターミナル/ログ
>   出力側**（`engine/runner.py` のスクリーニング結果テーブル、
>   `engine/trade_cycle.py:632`、`engine/agent_wrappers.py:256`）であり、
>   LINE通知（`engine/notify.py`）には反映されていない。ドキュメントを
>   信じて「LINEに根拠が出るはず」と調査すると見つからないので注意。
>   ドリフト詳細は `docs/DRIFT_CHECK.md` 参照。
> - 全銘柄HOLDでも**必ず1通送信**する設計（`send_line_notification()`
>   docstring）。
> - 送信先の宛先アイコンとして先頭に `[{hostname}]` を自動付与
>   （`send_line_message()`）— 本番/ローカルどちらから送られたか判別用。
> - `.env` の `LINE_ACCESS_TOKEN` / `LINE_USER_ID` が未設定なら送信を
>   スキップしログのみ出力するフェイルセーフあり。

## 開発環境

- Python 3.11 (venv) / Ubuntu 22.04 LTS
- 本番サーバー: uema2lab-search (Linux)
- ローカル開発: Lenovo ThinkPad E16 Gen 3 (32GB RAM / AMD Ryzen 9)

> **[検証済み追記 — 要ユーザー確認]**（2026-07-08）
> 本skill作成時にコマンドを実行した環境では `Python 3.14.4` /
> `Ubuntu 26.04 LTS` が確認された。ただし**これは本skill作成時にAIが
> コマンドを実行したサンドボックス環境の情報であり、ユーザーの物理
> ThinkPad開発機の実際のOS/Pythonバージョンと同一である保証はない**。
> 本番（`run_bot.sh` 内 venv）は `requirements.txt` の想定に従い
> Python 3.11系で運用されている可能性が高いが未確認。バージョン差異が
> 実害（RAGライブラリの互換性等）に繋がりうるため、次回ユーザー本人の
> 環境で `python3 --version` と `lsb_release -a` を確認し、この節を
> 確定情報に更新することを推奨する。

## このskillの範囲で変更作業をする際の注意

- **`run_bot.sh` は本番稼働に直結する。** 変更（実行順序、パス、フラグ）は
  必ずユーザー承認を得る。特に `git pull origin main` を挟む設計を崩すと
  本番が古いコードのまま動き続けるリスクがある。
- **`.env`（LINE_ACCESS_TOKEN, LINE_USER_ID, APCA_API_KEY_ID 等）は
  credentials であり読み書き禁止**（`.claude/skills/safety-guardrails/SKILL.md`
  にも同様の制約あり）。
- **crontab の設定自体はこのリポジトリ内で確認できていない**
  （本番ホスト側の `crontab -l` 出力が必要。ローカルリポジトリには
  crontab 定義ファイルが見つからなかった）。crontab の内容を断定的に
  書き換え提案しないこと。
- LINE通知フォーマットを変更する場合、`send_line_message()` の
  `[{hostname}]` プレフィックスと「全銘柄HOLDでも1通送信」の仕様を
  壊さないこと（運用上、通知の無音化に気づけなくなるため）。
- rsyncによる本番サーバー同期手順は本リポジトリ内に見つからなかった。
  `run_bot.sh` の `git pull` が実質的な同期機構であり、rsync運用は
  別途存在しない可能性が高い（ユーザーに確認が必要な場合は推測で
  記述しない）。
