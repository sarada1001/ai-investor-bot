# /lint-wiki

Wiki全体のヘルスチェックを実行します。

```bash
python scripts/lint_wiki.py
```

チェック内容:
1. **リンク切れ** — `[[リンク]]` の参照先ファイルが存在するか
2. **孤児ページ** — どこからもリンクされていないWikiページ
3. **矛盾検出** — 同一ティッカーの assessment がページ間で食い違っていないか
4. **鮮度** — last_updated が 7日以上古いティッカーページ
5. **未リンクLog** — obsidian_logs 内のログがWikiから参照されていないもの

Wikiが存在しない場合は先に `python server_librarian.py --ingest` を実行してください。
