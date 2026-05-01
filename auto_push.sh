#!/bin/bash

cd /home/naito/exa-investor || { echo "ERROR: ディレクトリ移動失敗"; exit 1; }

DATETIME=$(date '+%Y-%m-%d %H:%M:%S')

if [ -n "$(git status -s)" ]; then
    git add .
    git commit -m "auto-backup: ${DATETIME} (定期バックアップ)"
    git push origin main
    echo "[${DATETIME}] Push完了"
else
    echo "[${DATETIME}] 変更なし、スキップ"
fi
