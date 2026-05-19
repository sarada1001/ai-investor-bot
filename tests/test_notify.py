"""tests/test_notify.py — engine/notify のユニットテスト"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import engine.notify as notify_mod


def _posted_text(mock_post) -> str:
    """モックされた requests.post の送信テキストを取り出す。"""
    body = json.loads(mock_post.call_args.kwargs["data"])
    return body["messages"][0]["text"]


def _result(ticker: str, decision: str, score: float = 1.0, rationale: str = "テスト理由") -> dict:
    return {"ticker": ticker, "decision": decision, "score": score, "rationale": rationale}


@pytest.fixture(autouse=True)
def _patch_credentials(monkeypatch):
    """全テストで有効なLINE認証情報をデフォルトとして設定する。"""
    monkeypatch.setattr(notify_mod, "LINE_ACCESS_TOKEN", "test_token")
    monkeypatch.setattr(notify_mod, "LINE_USER_ID", "U_test_user")


class TestSendLineMessage:
    def test_skips_when_token_missing(self, monkeypatch):
        monkeypatch.setattr(notify_mod, "LINE_ACCESS_TOKEN", "")
        with patch("engine.notify.requests.post") as mock_post:
            notify_mod.send_line_message("hello")
            mock_post.assert_not_called()

    def test_skips_when_user_id_missing(self, monkeypatch):
        monkeypatch.setattr(notify_mod, "LINE_USER_ID", "")
        with patch("engine.notify.requests.post") as mock_post:
            notify_mod.send_line_message("hello")
            mock_post.assert_not_called()

    def test_text_is_included_in_payload(self):
        with patch("engine.notify.requests.post") as mock_post:
            notify_mod.send_line_message("テストメッセージ")
            mock_post.assert_called_once()
            assert "テストメッセージ" in _posted_text(mock_post)

    def test_hostname_is_prepended(self):
        with patch("engine.notify.requests.post") as mock_post:
            notify_mod.send_line_message("内容")
            text = _posted_text(mock_post)
            assert text.startswith("[")  # [hostname] プレフィックス


class TestSendLineNotification:
    def test_strong_buy_ticker_appears_in_message(self):
        results = [_result("AAPL", "STRONG BUY", 4.2), _result("TSCO", "HOLD", 0.5)]
        with patch("engine.notify.requests.post") as mock_post:
            notify_mod.send_line_notification(results)
            mock_post.assert_called_once()
            text = _posted_text(mock_post)
            assert "AAPL" in text
            assert "STRONG BUY" in text

    def test_buy_ticker_appears_in_message(self):
        results = [_result("MSFT", "BUY", 2.1), _result("NOC", "HOLD")]
        with patch("engine.notify.requests.post") as mock_post:
            notify_mod.send_line_notification(results)
            text = _posted_text(mock_post)
            assert "MSFT" in text
            assert "📈" in text

    def test_hold_tickers_listed_when_buy_exists(self):
        results = [_result("AAPL", "STRONG BUY"), _result("TSCO", "HOLD"), _result("NOC", "HOLD")]
        with patch("engine.notify.requests.post") as mock_post:
            notify_mod.send_line_notification(results)
            text = _posted_text(mock_post)
            assert "TSCO" in text
            assert "NOC" in text

    def test_all_hold_always_sends_notification(self):
        """全銘柄HOLDでも必ずPOSTが呼ばれること。"""
        results = [_result("LMT", "HOLD"), _result("CSGP", "HOLD"), _result("COO", "HOLD")]
        with patch("engine.notify.requests.post") as mock_post:
            notify_mod.send_line_notification(results)
            mock_post.assert_called_once()

    def test_all_hold_message_contains_summary(self):
        """全HOLDのとき「全銘柄HOLD」サマリーが含まれること。"""
        results = [_result("LMT", "HOLD"), _result("CSGP", "HOLD")]
        with patch("engine.notify.requests.post") as mock_post:
            notify_mod.send_line_notification(results)
            text = _posted_text(mock_post)
            assert "全銘柄HOLD" in text
            assert "LMT" in text
            assert "CSGP" in text

    def test_all_hold_message_excludes_buy_header(self):
        """全HOLDのとき、STRONG BUYヘッダーが含まれないこと。"""
        results = [_result("LMT", "HOLD")]
        with patch("engine.notify.requests.post") as mock_post:
            notify_mod.send_line_notification(results)
            text = _posted_text(mock_post)
            assert "STRONG BUY" not in text

    def test_empty_results_still_sends(self):
        """スクリーニング結果が0件でも通知が送られること。"""
        with patch("engine.notify.requests.post") as mock_post:
            notify_mod.send_line_notification([])
            mock_post.assert_called_once()

    def test_date_appears_in_header(self):
        """通知ヘッダーに日付が含まれること。"""
        with patch("engine.notify.requests.post") as mock_post:
            notify_mod.send_line_notification([])
            text = _posted_text(mock_post)
            assert "ECC スクリーニング結果" in text

    def test_rationale_truncated_to_60_chars(self):
        """rationaleが60文字で切り詰められること。"""
        long_reason = "あ" * 100
        results = [_result("AAPL", "BUY", rationale=long_reason)]
        with patch("engine.notify.requests.post") as mock_post:
            notify_mod.send_line_notification(results)
            text = _posted_text(mock_post)
            assert "あ" * 61 not in text
