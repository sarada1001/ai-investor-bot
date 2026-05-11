"""engine/notify.py — LINE 通知"""

from __future__ import annotations

import json
import os

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
    try:
        requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
            },
            data=json.dumps({"to": LINE_USER_ID,
                             "messages": [{"type": "text", "text": text}]}),
            timeout=10,
        )
    except Exception as e:
        _log(f"[LINE] 送信失敗: {e}")
