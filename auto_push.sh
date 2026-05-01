#!/bin/bash

cd /home/naito/exa-investor || { echo "ERROR: ディレクトリ移動失敗"; exit 1; }

DATETIME=$(date '+%Y-%m-%d %H:%M:%S')

if [ -n "$(git status -s)" ]; then
    README="README.md"
    SECTION="## 🔄 System Activity Log"
    LOG_ENTRY="- ⏱ **Backup \& Run:** ${DATETIME}"

    if ! grep -qF "${SECTION}" "${README}"; then
        printf '\n%s\n' "${SECTION}" >> "${README}"
    fi

    sed -i "/${SECTION//\//\\/}/a ${LOG_ENTRY}" "${README}"

    git add .
    git commit -m "auto-backup: ${DATETIME} (定期バックアップ)"
    git push origin main
    echo "[${DATETIME}] Push完了"
else
    echo "[${DATETIME}] 変更なし、スキップ"
fi
