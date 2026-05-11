"""engine/bbs.py — エージェント間共有テキストメモリ (Bulletin Board System)"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from engine.display import _log

BBS_DIR = Path("bbs")
BBS_DIR.mkdir(exist_ok=True)


class BBS:
    """テキストベースの共有メモリ。エージェントが順番に書き込む。"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.path = BBS_DIR / f"{session_id}.json"
        self._data: dict = {
            "session_id": session_id,
            "created_at": datetime.datetime.now().isoformat(),
            "entries": [],
        }
        self._save()

    def write(self, agent_name: str, key: str, data: object) -> None:
        # BBSスキーマのdataclassは自動的にdictに変換する
        if hasattr(data, "to_dict") and callable(data.to_dict):
            data = data.to_dict()
        entry = {
            "agent":     agent_name,
            "key":       key,
            "timestamp": datetime.datetime.now().isoformat(),
            "data":      data,
        }
        self._data["entries"].append(entry)
        self._save()
        _log(f"[BBS] {agent_name} → '{key}' 書き込み完了")

    def read(self, key: str) -> dict | str | None:
        for entry in reversed(self._data["entries"]):
            if entry["key"] == key:
                return entry["data"]
        return None

    def read_all(self) -> list[dict]:
        return self._data["entries"]

    def to_text_summary(self) -> str:
        lines = [f"=== BBS セッション {self.session_id} ==="]
        for e in self._data["entries"]:
            lines.append(f"\n--- [{e['agent']}] key={e['key']} ---")
            lines.append(json.dumps(e["data"], ensure_ascii=False, indent=2))
        return "\n".join(lines)

    def _save(self) -> None:
        BBS_DIR.mkdir(exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
