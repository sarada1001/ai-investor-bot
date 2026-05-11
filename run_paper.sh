#!/usr/bin/env bash
# run_paper.sh — ECC ペーパートレード実行スクリプト
#
# 使い方:
#   ./run_paper.sh                          # スクリーニング + 1サイクル (デフォルト)
#   ./run_paper.sh --preflight              # プリフライトチェックのみ
#   ./run_paper.sh --screen                 # スクリーニング + AI分析 + 発注 (paper)
#   ./run_paper.sh --daemon --screen        # 24時間デーモン起動
#   ./run_paper.sh --ticker AAPL            # 単一銘柄テスト
#   ./run_paper.sh --ticker AAPL --dry-run  # 発注なしドライラン
#
# 前提:
#   .env に ALPACA_PAPER_TRADING=True が設定済みであること

set -euo pipefail
cd "$(dirname "$0")"

VENV_PYTHON=".venv/bin/python"

if [[ ! -f "$VENV_PYTHON" ]]; then
    echo "❌ .venv が見つかりません。セットアップを確認してください。"
    exit 1
fi

# ── --preflight のみ実行して終了 ─────────────────────────────
if [[ "${1:-}" == "--preflight" ]]; then
    echo ""
    "$VENV_PYTHON" scripts/preflight_check.py
    exit $?
fi

# ── プリフライトチェック（サイレント失敗時は中断）────────────
echo ""
echo "── Pre-Flight Check ────────────────────────────────────"
if ! "$VENV_PYTHON" scripts/preflight_check.py 2>/dev/null; then
    echo "❌ プリフライトチェック FAILED。実行を中止します。"
    echo "   詳細: ./run_paper.sh --preflight"
    exit 1
fi

# ── メイン実行 ───────────────────────────────────────────────
echo "── Starting ECC ────────────────────────────────────────"
exec "$VENV_PYTHON" main.py "$@"
