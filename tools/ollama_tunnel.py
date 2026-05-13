"""Ollama SSHトンネル自動起動ユーティリティ"""
import logging
import os
import subprocess
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_OLLAMA_BASE = os.getenv("OLLAMA_ENDPOINT", "http://localhost:11434")
_TUNNEL_SCRIPT = Path(__file__).parent.parent / "scripts" / "start_ollama_tunnel.sh"
_MAX_WAIT = 25


def is_ollama_reachable(timeout: float = 2.0) -> bool:
    try:
        r = requests.get(f"{_OLLAMA_BASE}/api/tags", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def ensure_ollama_reachable() -> bool:
    """Ollamaが未到達なら自動でSSHトンネルを起動し、到達可能になるまで待機する。"""
    if is_ollama_reachable():
        return True

    print("[Tunnel] Ollama 未到達 → SSHトンネルを自動起動中 (uema2lab-gpu)...")
    logger.info("[Tunnel] Ollama 未到達 → SSHトンネルを自動起動")

    subprocess.Popen(
        ["bash", str(_TUNNEL_SCRIPT), "start"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    for elapsed in range(1, _MAX_WAIT + 1):
        time.sleep(1)
        if is_ollama_reachable():
            print(f"[Tunnel] Ollama 接続確立 ({elapsed}秒)")
            logger.info("[Tunnel] Ollama 接続確立 (%d秒)", elapsed)
            return True

    print(f"[Tunnel] 警告: {_MAX_WAIT}秒待機後も Ollama に到達できません。CriticAgent はスキップされます。")
    logger.warning("[Tunnel] %d秒待機後も Ollama 未到達", _MAX_WAIT)
    return False
