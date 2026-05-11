"""tests/test_training_data_collector.py — training_data_collector のユニットテスト"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch


def _make_judgment(
    is_strong_buy: bool = True,
    order: dict | None = None,
    mock_mode: bool = False,
    hybrid_mode: bool = False,
    current_price: float | None = None,
) -> dict:
    return {
        "decision": "STRONG BUY" if is_strong_buy else "HOLD",
        "is_strong_buy": is_strong_buy,
        "score": 0.75,
        "order": order or {},
        "current_price": current_price,
    }


class TestRegisterOpenPosition:
    """_register_open_position の entry_price 取得ロジック"""

    def _call(self, judgment: dict, tmp_path: Path) -> dict:
        from skills.training_data_collector import _register_open_position
        _register_open_position(
            record_id="test-id",
            ticker="AAPL",
            session_id="20260511_120000",
            judgment=judgment,
        )
        with open(tmp_path) as f:
            index = json.load(f)
        return index["AAPL"][-1]

    def test_fill_price_used_first(self, tmp_path):
        idx = tmp_path / "idx.json"
        idx.write_text("{}")
        order = {"fill_price": 195.0, "filled_avg_price": 194.0, "price": 193.0}
        j = _make_judgment(order=order, current_price=190.0)

        with patch("skills.training_data_collector.POSITIONS_INDEX", idx):
            entry = self._call(j, idx)

        assert entry["entry_price"] == 195.0

    def test_filled_avg_price_fallback(self, tmp_path):
        idx = tmp_path / "idx.json"
        idx.write_text("{}")
        order = {"fill_price": None, "filled_avg_price": 194.0}
        j = _make_judgment(order=order, current_price=190.0)

        with patch("skills.training_data_collector.POSITIONS_INDEX", idx):
            entry = self._call(j, idx)

        assert entry["entry_price"] == 194.0

    def test_current_price_last_fallback(self, tmp_path):
        idx = tmp_path / "idx.json"
        idx.write_text("{}")
        order = {"fill_price": None, "filled_avg_price": None, "price": None}
        j = _make_judgment(order=order, current_price=190.5)

        with patch("skills.training_data_collector.POSITIONS_INDEX", idx):
            entry = self._call(j, idx)

        assert entry["entry_price"] == 190.5

    def test_all_null_returns_none(self, tmp_path):
        idx = tmp_path / "idx.json"
        idx.write_text("{}")
        order = {}
        j = _make_judgment(order=order, current_price=None)

        with patch("skills.training_data_collector.POSITIONS_INDEX", idx):
            entry = self._call(j, idx)

        assert entry["entry_price"] is None


class TestSaveTrainingRecord:
    """save_training_record での _register_open_position 呼び出し条件"""

    def _save(self, judgment: dict, mock_mode: bool = False, hybrid_mode: bool = False):
        import skills.training_data_collector as tc
        import datetime

        calls = []
        orig = tc._register_open_position

        def capture(*args, **kwargs):
            calls.append((args, kwargs))

        with (
            patch.object(tc, "_register_open_position", side_effect=capture),
            patch.object(tc, "TRAINING_FILE", Path(tempfile.mktemp(suffix=".jsonl"))),
            patch.object(tc, "_ensure_dirs"),
        ):
            tc.save_training_record(
                session_id="20260511_120000",
                ticker="AAPL",
                bbs_entries=[],
                judgment=judgment,
                mock_mode=mock_mode,
                hybrid_mode=hybrid_mode,
            )
        return calls

    def test_real_buy_registers_position(self):
        order = {"success": True, "fill_price": 195.0}
        j = _make_judgment(is_strong_buy=True, order=order)
        calls = self._save(j, mock_mode=False, hybrid_mode=False)
        assert len(calls) == 1

    def test_dry_run_order_does_not_register(self):
        order = {"dry_run": True, "symbol": "AAPL", "qty": 5}
        j = _make_judgment(is_strong_buy=True, order=order)
        calls = self._save(j, mock_mode=False, hybrid_mode=False)
        assert len(calls) == 0

    def test_mock_mode_does_not_register(self):
        order = {"success": True, "fill_price": 195.0}
        j = _make_judgment(is_strong_buy=True, order=order)
        calls = self._save(j, mock_mode=True, hybrid_mode=False)
        assert len(calls) == 0

    def test_hybrid_mode_does_not_register(self):
        order = {"success": True, "fill_price": 195.0}
        j = _make_judgment(is_strong_buy=True, order=order)
        calls = self._save(j, mock_mode=False, hybrid_mode=True)
        assert len(calls) == 0

    def test_skipped_order_does_not_register(self):
        order = {"success": False, "skipped": True, "skip_reason": "重複注文防止"}
        j = _make_judgment(is_strong_buy=True, order=order)
        calls = self._save(j, mock_mode=False, hybrid_mode=False)
        assert len(calls) == 0

    def test_hold_does_not_register(self):
        j = _make_judgment(is_strong_buy=False, order={})
        calls = self._save(j, mock_mode=False, hybrid_mode=False)
        assert len(calls) == 0
