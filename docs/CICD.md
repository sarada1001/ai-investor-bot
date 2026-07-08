# CI/CD パイプライン説明

> 対象ファイル: `.github/workflows/ci.yml`, `requirements-ci.txt`
> 作成日: 2026-07-02　｜　実装（ワークフローYAML）を直接読んで作成した現状記録。

## 概要

GitHub Actions によるCI（継続的インテグレーション）のみを実装。CD（自動デプロイ）は本システムには
存在しない — 本番反映は手動 `git push` によるスケジューラーノードへの同期（[インフラ構成](../README.md#インフラ構成)参照）。

## トリガー

| イベント | 条件 |
|---|---|
| push | `main` または `dev` ブランチへのpush |
| pull_request | `main` へのPR作成・更新 |
| schedule | 毎日 `00:00 UTC`（09:00 JST）のナイトリー実行（cron: `0 0 * * *`） |

## ジョブ構成（1ジョブ: `test`）

実行環境: `ubuntu-latest` / Python 3.12（matrix、現状1バージョンのみ）

### ステップ

1. **Checkout** — `actions/checkout@v4`
2. **Python セットアップ** — `actions/setup-python@v5`
3. **pipキャッシュ** — `requirements-ci.txt` / `requirements.txt` のハッシュをキーにキャッシュ
   （変更がなければ再インストールをスキップし高速化）
4. **依存インストール** — `pip install -r requirements-ci.txt`
   （`requirements.txt` を継承 + `pytest-cov` / `rich` をテスト用に追加）
5. **ダミー `.env` 生成** — APIキー不要のユニットテストを通すため、CI専用のダミー値を書き込む
   （`GOOGLE_API_KEY`, `ALPACA_API_KEY` 等はすべて `dummy-ci-*`。**本物の秘密情報はCI上に一切存在しない**）
6. **テスト実行**:
   ```bash
   pytest tests/ \
     -m "not integration and not slow" \
     --cov=agents --cov=engine --cov=skills --cov=tools \
     --cov-report=term-missing \
     --cov-report=xml:coverage.xml \
     --cov-fail-under=30
   ```
7. **カバレッジレポートのアップロード** — `coverage.xml` を7日間アーティファクト保持（`if: always()`のため
   テスト失敗時もレポートは残る）

## テスト実行スコープの注意点

CI上では `-m "not integration and not slow"` により **integration/slowマーカー付きテストは除外**される。
`pytest.ini` に定義されたマーカー:

| マーカー | 意味 |
|---|---|
| `unit` | 外部API非依存の純粋ユニットテスト |
| `integration` | 実APIクレデンシャルや外部サービスが必要なテスト |
| `slow` | 30秒以上かかるテスト（モデルロード・ネットワーク等） |

`docs/TEST_REPORT.md`（`scripts/gen_test_report.py` で生成）は**マーカーによる除外なしの全334件**を
ローカル実行した結果である点に注意。CI上で実際に走る件数はこれより少ない可能性がある
（現状のテストスイートに `integration`/`slow` マーカーが付与されたテストは見当たらず、実質的には
ほぼ同数と推定されるが、マーカー漏れがあれば乖離しうる）。

## カバレッジゲート

`--cov-fail-under=30` — カバレッジが30%を下回るとCI失敗。対象は `agents` / `engine` / `skills` / `tools`
の4パッケージ（`dashboard/` や `scripts/` はカバレッジ集計対象外）。

## CD（本番反映）について

本リポジトリにデプロイジョブは存在しない。本番反映は以下の手動フローで行われている
（[README.md § インフラ構成](../README.md#インフラ構成)より）:

1. 開発ノード（WSL2）でコード変更 → `git push`
2. スケジューラーノード（`uema2lab-search`）が最新コードを取得（同期方式はcron/手動pull、
   本ドキュメントの調査範囲外）
3. cron `23:00 JST` の `auto_push.sh` が自動コミット・プッシュ（データ側の同期）

GitHub Actionsのnightly実行は「本番に影響する前に壊れていないか検知する」ためのものであり、
そのままデプロイをトリガーするものではない。
