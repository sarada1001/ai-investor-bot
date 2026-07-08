"""tests/test_social_monitor.py — SocialAgent StockTwits実装のスモークテスト

StockTwits公開APIは認証不要のため、integration マーカー付きテストは
実接続で実行する。CI/オフライン環境では `-m "not integration"` で除外できる。
"""
from __future__ import annotations

import pytest

from skills.social_monitor import _fetch_stocktwits, fetch_social_sentiment


@pytest.mark.integration
def test_fetch_stocktwits_returns_posts_for_valid_ticker():
    posts = _fetch_stocktwits("AAPL")
    assert isinstance(posts, list)
    assert len(posts) > 0
    assert all(isinstance(p, str) for p in posts)


@pytest.mark.integration
def test_fetch_stocktwits_raises_for_invalid_ticker():
    with pytest.raises(Exception):
        _fetch_stocktwits("ZZZZZINVALID9999")


@pytest.mark.integration
def test_fetch_social_sentiment_falls_back_gracefully_on_invalid_ticker():
    result = fetch_social_sentiment("ZZZZZINVALID9999")
    assert result["sentiment"] == "NEUTRAL"
    assert result["source"] == "unavailable"
    assert -1.0 <= result["score"] <= 1.0
    assert 0.0 <= result["hype_score"] <= 1.0
    assert result["post_count"] == 0


def test_fetch_social_sentiment_mock_mode(monkeypatch):
    """SOCIAL_USE_MOCK=true 時にモック判定ロジックが有効になることを確認"""
    monkeypatch.setenv("SOCIAL_USE_MOCK", "true")
    from skills.social_monitor import _is_mock_enabled

    assert _is_mock_enabled() is True
