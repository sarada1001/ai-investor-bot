#!/bin/bash

cd /home/naito/exa-investor || { echo "ERROR: ディレクトリ移動失敗"; exit 1; }

DATETIME=$(date '+%Y-%m-%d %H:%M:%S')
COMMIT_MSG="auto-backup: ${DATETIME} (定期バックアップ)"

if [ -n "$(git status -s)" ]; then
    git add .
    git commit -m "${COMMIT_MSG}"
    COMMIT_HASH=$(git rev-parse HEAD)

    README="README.md"
    SECTION="## 🔄 Development History"
    LOG_ENTRY="- 📅 **${DATETIME}** | 🛠️ **内容:** \`${COMMIT_MSG}\` | [🔍 変更箇所を確認](https://github.com/sarada1001/ai-investor-bot/commit/${COMMIT_HASH})"

    if ! grep -qF "${SECTION}" "${README}"; then
        printf '\n%s\n' "${SECTION}" >> "${README}"
    fi

    awk -v entry="${LOG_ENTRY}" '
        /^## 🔄 Development History$/ { print; print entry; next }
        { print }
    ' "${README}" > "${README}.tmp" && mv "${README}.tmp" "${README}"

    git add "${README}"
    git commit -m "docs: development history を更新 [${DATETIME}]"
    if git push origin main; then
        echo "[${DATETIME}] Push完了"
    else
        echo "[${DATETIME}] ERROR: git push 失敗（認証・ネットワークを確認してください）"
        exit 1
    fi
else
    echo "[${DATETIME}] 変更なし、スキップ"
fi
