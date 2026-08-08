"""tests/conftest.py — 全テスト共通のフィクスチャ

## なぜこのファイルが必要か

`tests/test_research_mode.py` の research_mode テストが `run_trade_cycle(mock_mode=True)`
を実行した際、Stage 0 の Selling Loop（ExitAgent）が**本番の** `data/portfolio.json` を
空にし、偽の Obsidian 売却ログを生成する事故が起きた。

`mock_mode=True` は LLM 呼び出しをスキップするだけで、
**yfinance の価格取得とファイル書き込みは止めない**。

そのため個別テストを直すのではなく、`autouse` フィクスチャで
**全テストのデフォルトを「本番パスに触れない」状態**にする。
新しく追加されたテストが本番コードパスを踏んでも、書き込み先は tmp に閉じる。

## 対象を増やすとき

`_REDIRECTED_PATHS` に (モジュール, 属性名, tmp 配下の相対パス) を足すだけでよい。
`tests/test_state_file_isolation.py` が定数一覧を検証しているので、
張り替え漏れ・fixture の破損はそちらで検知できる。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 本番の状態ファイル置き場。ここへ書き込みが起きたらテストの失敗とみなす。
PRODUCTION_DATA_DIR = PROJECT_ROOT / "data"

# (モジュール名, 属性名, tmp_path 配下の相対パス)
#
# いずれもモジュールグローバルを参照時に解決するコードなので、
# monkeypatch.setattr で張り替えれば呼び出し側の修正は不要。
# 例外は tools.auto_logger.SAVE_DIR で、これは ObsidianLogger.__init__ が
# 読むため「fixture 適用後にインスタンス化される」ことが前提になる
# （テスト本体で生成される限り常に成立する）。
_REDIRECTED_PATHS: tuple[tuple[str, str, str], ...] = (
    ("agents.exit_agent",              "PORTFOLIO_PATH",  "data/portfolio.json"),
    ("agents.exit_agent",              "OBSIDIAN_LOGS",   "data/knowledge_base/obsidian_logs"),
    ("tools.auto_logger",              "SAVE_DIR",        "data/knowledge_base/obsidian_logs"),
    ("skills.training_data_collector", "TRAINING_DIR",    "data/training"),
    ("skills.training_data_collector", "TRAINING_FILE",   "data/training/training_data.jsonl"),
    ("skills.training_data_collector", "POSITIONS_INDEX", "data/training/open_positions_index.json"),
    # portfolio_monitor は読み取り専用だが、本番ポートフォリオの内容で
    # テスト結果が変わらないよう同じ tmp に向けておく。
    ("skills.portfolio_monitor",       "_PORTFOLIO_PATH", "data/portfolio.json"),
    # BBS_DIR は CWD 相対の Path("bbs")。張り替えないと run_trade_cycle を
    # 呼ぶテストが本番 bbs/ にセッションファイルを増やし続ける。
    ("engine.bbs",                     "BBS_DIR",         "bbs"),
)

# 事前に mkdir しておくディレクトリ（tmp ルートからの相対パス）。
# 書き込み側が mkdir しないケース（例: _save_positions_index）を救うため。
#
# ※ "bbs" は意図的に含めない。BBS._save() が自分で mkdir する上、
#   test_bbs_schema.py が `(tmp_path / "bbs").mkdir()` と exist_ok なしで
#   呼んでおり、先回りして作ると FileExistsError で落ちるため。
_TMP_DIRS: tuple[str, ...] = (
    "data",
    "data/knowledge_base/obsidian_logs",
    "data/training",
)


@pytest.fixture(autouse=True)
def isolate_state_files(monkeypatch, tmp_path) -> Path:
    """
    本番の状態ファイルパスを tmp_path 配下へ張り替える（全テストに自動適用）。

    Returns:
        張り替え先のルートディレクトリ。テストから
        `isolate_state_files` を引数に取れば参照できる。
    """
    for rel in _TMP_DIRS:
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)

    for module_name, attr, rel_path in _REDIRECTED_PATHS:
        # import 失敗は握りつぶさない。握りつぶすと「守っているつもりで
        # 本番パスのまま」という最悪のケースを生むため。
        module = importlib.import_module(module_name)
        if not hasattr(module, attr):
            raise AttributeError(
                f"conftest の張り替え対象 {module_name}.{attr} が存在しません。"
                f" 定数がリネーム/削除された可能性があります"
                f"（tests/conftest.py の _REDIRECTED_PATHS を更新してください）"
            )
        monkeypatch.setattr(module, attr, tmp_path / rel_path)

    return tmp_path
