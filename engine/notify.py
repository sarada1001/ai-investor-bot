"""engine/notify.py — LINE 通知"""

from __future__ import annotations

import datetime
import json
import os
import socket

import requests
from dotenv import load_dotenv

from engine.display import _log

load_dotenv()

LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN", "")
LINE_USER_ID      = os.getenv("LINE_USER_ID", "")


def send_line_message(text: str) -> None:
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID:
        _log("[LINE] スキップ: LINE_ACCESS_TOKEN または LINE_USER_ID が未設定 (.env を確認)")
        return
    hostname = socket.gethostname()
    tagged_text = f"[{hostname}]\n{text}"
    try:
        resp = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
            },
            data=json.dumps({"to": LINE_USER_ID,
                             "messages": [{"type": "text", "text": tagged_text}]}),
            timeout=10,
        )
        if resp.status_code != 200:
            _log(f"[LINE] 送信失敗: HTTP {resp.status_code} — {resp.text}")
    except Exception as e:
        _log(f"[LINE] 送信失敗: {e}")


def send_line_notification(results: list[dict]) -> None:
    """スクリーニング結果をLINEに送信する。全銘柄HOLDでも必ず1通送信する。"""
    today = datetime.date.today().strftime("%Y-%m-%d")
    total = len(results)

    buy_results  = [r for r in results if r.get("decision") in ("BUY", "STRONG BUY")]
    hold_results = [r for r in results if r.get("decision") not in ("BUY", "STRONG BUY")]

    lines = [f"【ECC スクリーニング結果】{today}"]

    if buy_results:
        lines.append(f"{total}銘柄を分析 | 通過: {len(buy_results)}銘柄\n")
        for r in buy_results:
            decision  = r.get("decision", "BUY")
            ticker    = r.get("ticker", "?")
            score     = r.get("score", 0.0)
            rationale = r.get("rationale", "")[:60]
            icon      = "🚀" if decision == "STRONG BUY" else "📈"
            lines.append(f"{icon} {ticker}  {decision}  スコア {score:+.4f}")
            if rationale:
                lines.append(f"   理由: {rationale}")
        if hold_results:
            hold_tickers = ", ".join(r.get("ticker", "?") for r in hold_results)
            lines.append(f"\n⏸ HOLD ({len(hold_results)}銘柄): {hold_tickers}")
    else:
        lines.append(f"{total}銘柄を分析 | 本日は全銘柄HOLD（見送り）\n")
        hold_tickers = ", ".join(r.get("ticker", "?") for r in results)
        lines.append(f"⏸ HOLD ({total}銘柄): {hold_tickers}")

    send_line_message("\n".join(lines))
