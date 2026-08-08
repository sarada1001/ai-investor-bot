"""tests/test_state_file_isolation.py — 本番状態ファイルへの書き込み防止の安全ネット

`tests/conftest.py` の autouse フィクスチャ `isolate_state_files` が
壊れた／張り替え漏れが生じた場合に、それを検知するためのテスト。

過去に `run_trade_cycle(mock_mode=True)` を呼ぶテストが本番の
`data/portfolio.json` を空にし、偽の Obsidian 売却ログを生成した事故があった。
同種の事故を「テストの失敗」として即座に表面化させるのが目的。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.conftest import (  # noqa: E402
    PRODUCTION_DATA_DIR,
    _REDIRECTED_PATHS,
)

PROJECT_ROOT = PRODUCTION_DATA_DIR.parent

# 本番側で「テスト実行によって絶対に変化してはいけない」対象
_PROTECTED: tuple[Path, ...] = (
    PRODUCTION_DATA_DIR / "portfolio.json",
    PRODUCTION_DATA_DIR / "knowledge_base" / "obsidian_logs",
    PRODUCTION_DATA_DIR / "training",
    PROJECT_ROOT / "bbs",
)


def _snapshot(targets: tuple[Path, ...]) -> dict[str, tuple[int, int]]:
    """対象ファイル／ディレクトリ配下の (サイズ, mtime_ns) を採取する。"""
    snap: dict[str, tuple[int, int]] = {}
    for target in targets:
        if target.is_dir():
            paths = sorted(p for p in target.rglob("*") if p.is_file())
        elif target.is_file():
            paths = [target]
        else:
            continue
        for p in paths:
            st = p.stat()
            snap[str(p)] = (st.st_size, st.st_mtime_ns)
    return snap


def test_production_state_files_are_never_written(isolate_state_files, tmp_path):
    """
    本番コードパスで実際に書き込みを行っても、本番 data/ が一切変化しないこと。

    conftest の張り替えが効いていなければ、この 1 件が失敗して
    「本番ファイルを汚した」ことが分かる。
    """
    import agents.exit_agent as exit_agent
    import skills.training_data_collector as tdc
    from engine.bbs import BBS
    from tools.auto_logger import ObsidianLogger

    # ── 1. 張り替え先が tmp 配下であり、本番 data/ を指していないこと
    for module_name, attr, _rel in _REDIRECTED_PATHS:
        module = sys.modules[module_name]
        current = Path(getattr(module, attr))
        assert current.is_relative_to(tmp_path), (
            f"{module_name}.{attr} が tmp 配下を指していません: {current}"
        )
        assert not current.is_relative_to(PRODUCTION_DATA_DIR), (
            f"{module_name}.{attr} が本番 data/ を指しています: {current}"
        )

    # ── 2. 本番 data/ のスナップショットを採取
    before = _snapshot(_PROTECTED)

    # ── 3. 本番コードパスを通して実際に書き込む
    exit_agent.add_position(
        ticker="ZZZZ", entry_price=100.0, shares=1,
        target_price=110.0, stop_loss_price=95.0,
    )
    ObsidianLogger().save_log({
        "ticker":  "ZZZZ",
        "action":  "BUY",
        "context": "state isolation safety net",
    })
    tdc._save_positions_index({"ZZZZ": [{"record_id": "test", "entry_price": 100.0}]})
    BBS("19700101_000000").write("TestAgent", "technical_analysis", {"trend": "neutral"})

    # ── 4. 書き込みが tmp 側に着地していること（= 経路が生きていること）
    assert (tmp_path / "data" / "portfolio.json").exists()
    assert list((tmp_path / "data" / "knowledge_base" / "obsidian_logs").glob("*.md"))
    assert (tmp_path / "data" / "training" / "open_positions_index.json").exists()
    assert (tmp_path / "bbs" / "19700101_000000.json").exists()

    # ── 5. 本番 data/ は 1 バイトも変化していないこと
    after = _snapshot(_PROTECTED)
    assert after == before, (
        "テスト実行が本番の状態ファイルを変更しました。"
        f" 差分: {sorted(set(after.items()) ^ set(before.items()))}"
    )


def test_redirect_targets_still_exist():
    """
    張り替え対象の定数が実装側に存在し続けていること。

    定数がリネームされると conftest が AttributeError を出すが、
    そちらは全テストが落ちるため原因が分かりにくい。ここで名指しで落とす。
    """
    import importlib

    for module_name, attr, _rel in _REDIRECTED_PATHS:
        module = importlib.import_module(module_name)
        assert hasattr(module, attr), (
            f"{module_name}.{attr} が存在しません。"
            f" tests/conftest.py の _REDIRECTED_PATHS を更新してください"
        )
