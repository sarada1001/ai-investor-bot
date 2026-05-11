#!/usr/bin/env bash
# scripts/start_daemon.sh — ECC デーモン起動スクリプト（重複起動防止付き）
#
# 使い方:
#   ./scripts/start_daemon.sh            # デーモン起動（既に起動中なら何もしない）
#   ./scripts/start_daemon.sh --status   # 稼働状態の確認
#   ./scripts/start_daemon.sh --stop     # デーモンを停止
#
# cronから呼ぶ想定:
#   @reboot  sleep 30 && /path/start_daemon.sh >> /path/logs/cron_daemon.log 2>&1
#   0 * * * *           /path/start_daemon.sh >> /path/logs/cron_daemon.log 2>&1

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$DIR/logs"
LOGFILE="$LOG_DIR/daemon_$(date -u +%Y-%m-%d).log"
TIMESTAMP="[$(date -u '+%Y-%m-%d %H:%M:%S UTC')]"

# ── --status ─────────────────────────────────────────────────
if [[ "${1:-}" == "--status" ]]; then
    if pgrep -f "main.py.*--daemon" > /dev/null 2>&1; then
        PID=$(pgrep -f "main.py.*--daemon" | head -1)
        echo "$TIMESTAMP ECC daemon 稼働中 (PID=$PID)"
    else
        echo "$TIMESTAMP ECC daemon 停止中"
    fi
    exit 0
fi

# ── --stop ────────────────────────────────────────────────────
if [[ "${1:-}" == "--stop" ]]; then
    if pgrep -f "main.py.*--daemon" > /dev/null 2>&1; then
        pkill -f "main.py.*--daemon" && echo "$TIMESTAMP ECC daemon を停止しました"
    else
        echo "$TIMESTAMP ECC daemon は稼働していません"
    fi
    exit 0
fi

# ── 重複起動チェック ──────────────────────────────────────────
if pgrep -f "main.py.*--daemon" > /dev/null 2>&1; then
    # 静かに終了（cron の watchdog が毎時間叩くため出力しない）
    exit 0
fi

# ── ログディレクトリ作成 ──────────────────────────────────────
mkdir -p "$LOG_DIR"

# ── デーモン起動 ──────────────────────────────────────────────
cd "$DIR"
echo "$TIMESTAMP ECC daemon を起動します (log=$LOGFILE)"
nohup bash run_paper.sh --daemon --screen --notify-line >> "$LOGFILE" 2>&1 &
DAEMON_PID=$!
echo "$TIMESTAMP ECC daemon 起動完了 (PID=$DAEMON_PID)"
