#!/bin/bash

cd /home/naito/ai-investor-bot || { echo "ERROR: ディレクトリ移動失敗"; exit 1; }

DATETIME=$(date '+%Y-%m-%d %H:%M:%S')
COMMIT_MSG="auto-backup: ${DATETIME} (定期バックアップ)"

if [ -n "$(git status -s)" ]; then
    # Obsidian ログ（reflexion logs）を専用メッセージで先にコミット
    OBSIDIAN_DIR="data/knowledge_base/obsidian_logs"
    if [ -n "$(git status -s "${OBSIDIAN_DIR}" 2>/dev/null)" ]; then
        git add "${OBSIDIAN_DIR}"
        LOG_COUNT=$(git diff --cached --name-only | grep -c "\.md$" || true)
        git commit -m "logs: add reflexion log(s) [${DATETIME}] (${LOG_COUNT} file(s))"
        echo "[${DATETIME}] Obsidian ログをコミット (${LOG_COUNT} ファイル)"
    fi

    # 残りの変更を一括バックアップ
    if [ -z "$(git status -s)" ]; then
        echo "[${DATETIME}] ログのみ変更 — バックアップコミットをスキップ"
        COMMIT_HASH=$(git rev-parse HEAD)
    else
        git add .
        git commit -m "${COMMIT_MSG}"
        COMMIT_HASH=$(git rev-parse HEAD)
    fi

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
