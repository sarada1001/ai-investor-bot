#!/bin/bash
set -euo pipefail

export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

BOT_DIR=/home/naito/ai-investor-bot
PYTHON=$BOT_DIR/venv/bin/python3

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*"
}

log "INFO  [run_bot] === START ==="

cd "$BOT_DIR"

if git pull origin main 2>&1; then
    log "INFO  [git-pull] OK"
else
    log "ERROR [git-pull] FAILED — running with stale code"
fi

log "INFO  [main.py] starting --screen --notify-line"
"$PYTHON" main.py --screen --notify-line
log "INFO  [main.py] done"

log "INFO  [run_bot] === END ==="
