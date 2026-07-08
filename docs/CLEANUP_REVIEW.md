# 4項目 手動レビュー調査レポート

> 保守的クリーンアップ（3コミット済み）で「手動レビュー待ち」として保留された4ファイルの参照調査。
> **read-onlyの調査結果のみ。削除・変更は一切行っていない。最終判断は開発者本人が行うこと。**
> 調査日: 2026-07-02　｜　調査方法: `grep`によるインポート/参照走査 + `git log`による最終更新日確認

## サマリー

| ファイル | 参照数 | 最終更新 | テスト | 判定候補 |
|---|---|---|---|---|
| `dashboard.py` | README本文で現役ドキュメント化 + 稼働中 | 2026-05-13 | なし | **KEEP（ただし新dashboard/との関係整理が必要）** |
| `monitor.py` | README本文で現役ドキュメント化 + 稼働中 | 2026-05-13 | `tests/test_monitor.py`（19件） | **KEEP（判断に迷いなし）** |
| `skills/portfolio_tracker.py` | **どこからもimportされていない** | 2026-04-29 | なし | **ARCHIVE候補** |
| `tools/ollama_tunnel.py` | **どこからもimportされていない** | 2026-05-14（自動バックアップコミットのみ） | なし | **ARCHIVE候補** |

---

## 1. `dashboard.py` — KEEP（要整理）

**参照**: README.md に専用セクション「dashboard.py — Streamlit Web UI」があり、`streamlit run dashboard.py`
として現役の起動コマンドが案内されている。`docs/architecture.md` のディレクトリ構造にも記載あり。

**データソース**: `data/training/training_data.jsonl`・`data/portfolio.json` を直接読む1073行の単一ファイル実装。

**新 `dashboard/` パッケージとの関係**: 今回のポートフォリオ整備で拡張した `dashboard/app.py`（10セクション、
DuckDB + Parquet経由で `data/analytics/*.parquet` を読む）とは**データソースも実装方式も別物**。
`dashboard.py`（レガシー単一ファイル）と `dashboard/`（新Decision Analytics Dashboard）が並存しており、
README.en.mdでは便宜上「legacy single-file version」と注記したが、**READMEの本文（日本語版）ではこの
使い分けが未整理**（新dashboard/への言及がまだ無い）。

**推奨**: 削除は不要。ただし README.md 側にも「2つのダッシュボードがあり、用途が異なる」ことを明記する
追記が必要（次回作業候補）。どちらを「正」として就活資料に載せるかは開発者の判断が必要。

## 2. `monitor.py` — KEEP（判断に迷いなし）

**参照**: README本文で現役ドキュメント化。`tmux`常駐運用の案内あり。インフラ構成図でも
「② スケジューラーノード」上で常時稼働するTUIとして明記。

**テスト**: `tests/test_monitor.py` に19件のユニットテストがあり、全てPASSED（`docs/TEST_REPORT.md`参照）。

**推奨**: レビュー対象から外してよい。dashboard.py/dashboard/とは別軸（ターミナルTUI vs Web UI）の
ツールであり機能重複もない。

## 3. `skills/portfolio_tracker.py` — ARCHIVE候補

**参照**: `grep`で全リポジトリを走査した結果、**自分自身以外からのimportが1件も見つからなかった**。
README.md のディレクトリ構造一覧に説明行（「P&L計算」）があるのみで、実行経路（`main.py` / `engine/` /
`skills/__init__.py`）からは一切参照されていない。

**類似機能との重複**: 同じ `skills/` 内に `portfolio_monitor.py` があり、こちらは
`skills/__init__.py` の `SKILLS` レジストリに `"portfolio_monitor": _portfolio_monitor_run` として
**実際に登録・使用されている**。ファイル名・役割（ポートフォリオのP&L/状態追跡）が酷似しており、
`portfolio_tracker.py` は初期実装（2026-04-29、最古参の部類）が `portfolio_monitor.py` に置き換えられた
後の残骸である可能性が高い。

**推奨**: `git blame`/コミットメッセージ「add: Alpaca paper trading execution + signal scoring +
portfolio tracking」（2026-04-29）と `portfolio_monitor.py` の導入時期を突き合わせて、開発者本人が
「置き換えられた旧実装」と確認できれば、`archive/` への退避（削除ではなく移動）を推奨。

## 4. `tools/ollama_tunnel.py` — ARCHIVE候補

**参照**: **自分自身以外からのimportが1件も見つからなかった**。コード内では逆に
`scripts/start_ollama_tunnel.sh`（シェルスクリプト）のパスを参照しているが、この
Pythonラッパー自体を呼び出す側のコードはリポジトリ内に存在しない。

**独立して動くシェルスクリプトの存在**: `scripts/start_ollama_tunnel.sh` は単体で
`./scripts/start_ollama_tunnel.sh [stop|status]` として動作する独立スクリプトであり、
Ollamaトンネル起動という実務上の目的はこちらだけで完結している可能性が高い。

**最終更新**: 2026-05-14の「定期バックアップ」コミットのみで、機能追加としての実質的な最終更新はさらに遡る。

**推奨**: `tools/ollama_tunnel.py` がPythonから呼ばれる想定（例: 将来的にmain.py起動時に自動でトンネルを
張る計画があった等）だったのか、単なる初期プロトタイプかを開発者に確認のうえ、不要であれば `archive/` へ。

---

## 次のアクション（開発者の判断が必要な箇所）

1. `skills/portfolio_tracker.py` と `skills/portfolio_monitor.py` の履歴を見比べ、後者への統合が完了しているか確認
2. `tools/ollama_tunnel.py` が今後使う予定のあるコードか確認（無ければ `archive/` へ退避）
3. `dashboard.py`（レガシー）と `dashboard/`（新Decision Analytics Dashboard）の使い分けをREADME.mdに追記するか、
   あるいは `dashboard.py` の機能を段階的に `dashboard/` へ統合していくかの方針を決める
4. `monitor.py` はレビュー対象から除外してよい

**注記**: 本レポートはgrepベースの静的参照調査であり、動的import（`importlib`等）やシェルからの直接呼び出し
（例: `python -c "from skills.portfolio_tracker import ..."`のようなアドホック実行）までは検出できない。
削除前には開発者自身による最終確認を推奨する。
