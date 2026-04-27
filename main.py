"""
main.py — スイングトレード金融マルチエージェントシステム
ECC (everything-claude-code) アーキテクチャ

実行フロー:
  NewsAgent → FundamentalAgent → TechnicalAgent
      ↓ (全員がBBSに書き込む)
  ManagerAgent (BBS読み取り → 統合判断 → BBS書き込み)
      ↓
  ComplianceAgent (BBS読み取り → 検閲 → 最終決定)
      ↓
  LINE通知（最終判断のみ）
"""

import os
import sys
import json
import time
import datetime
import requests
import warnings
import yaml
from pathlib import Path
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

warnings.filterwarnings("ignore")
load_dotenv()

# --- 内部モジュール ---
sys.path.insert(0, str(Path(__file__).parent))
import skills.news_monitor as _news_monitor_mod
import skills.rag_search as _rag_search_mod
import skills.technical_calc as _technical_calc_mod

# =========================================================
# BBS (Bulletin Board System) — 共有テキストメモリ
# =========================================================
BBS_DIR = Path("bbs")
BBS_DIR.mkdir(exist_ok=True)


class BBS:
    """テキストベースの共有メモリ。エージェントが順番に書き込む。"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.path = BBS_DIR / f"{session_id}.json"
        self._data: dict = {
            "session_id": session_id,
            "created_at": datetime.datetime.now().isoformat(),
            "entries": [],
        }
        self._save()

    def write(self, agent_name: str, key: str, data: dict | str) -> None:
        entry = {
            "agent": agent_name,
            "key": key,
            "timestamp": datetime.datetime.now().isoformat(),
            "data": data,
        }
        self._data["entries"].append(entry)
        self._save()
        print(f"  [BBS] {agent_name} → '{key}' を書き込みました。")

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
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)


# =========================================================
# Agent基底クラス
# =========================================================

def _load_agent_config(agent_name: str) -> dict:
    path = Path(".agents") / f"{agent_name}.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


class BaseAgent:
    def __init__(self, config_name: str, bbs: BBS):
        self.config = _load_agent_config(config_name)
        self.name = self.config["name"]
        self.bbs = bbs
        self.llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)

    def run(self) -> dict:
        raise NotImplementedError


# =========================================================
# 1. NewsAgent
# =========================================================

class NewsAgent(BaseAgent):
    def __init__(self, bbs: BBS):
        super().__init__("news_agent", bbs)
        # 許可スキルの検証
        assert "news_monitor" in self.config["allowed_skills"], \
            f"{self.name}: news_monitor へのアクセス権がありません"

    def run(self) -> dict:
        print(f"\n[{self.name}] ニュース監視を開始します...")
        companies = self.config["params"]["companies"]

        # Skill呼び出し（許可されたスキルのみ）
        result = _news_monitor_mod.run(companies=companies)

        self.bbs.write(self.name, self.config["output_bbs_key"], result)
        print(f"  [{self.name}] 新着記事: {result['new_count']}件")
        return result


# =========================================================
# 2. FundamentalAgent
# =========================================================

class FundamentalAgent(BaseAgent):
    def __init__(self, bbs: BBS):
        super().__init__("fundamental_agent", bbs)
        assert "rag_search" in self.config["allowed_skills"], \
            f"{self.name}: rag_search へのアクセス権がありません"

    def run(self) -> dict:
        print(f"\n[{self.name}] ファンダメンタルズ分析を開始します...")
        params = self.config["params"]
        queries = params.get("default_queries", [])
        persist_dir = params.get("persist_dir", "chroma_db_saved")
        top_k = params.get("top_k", 6)

        analyses = []
        for q in queries:
            result = _rag_search_mod.run(query=q, persist_dir=persist_dir, top_k=top_k)
            analyses.append({"query": q, "analysis": result["analysis"]})
            time.sleep(2)

        output = {"queries": analyses, "persist_dir": persist_dir}
        self.bbs.write(self.name, self.config["output_bbs_key"], output)
        print(f"  [{self.name}] {len(analyses)}件のクエリ分析完了。")
        return output


# =========================================================
# 3. TechnicalAgent
# =========================================================

class TechnicalAgent(BaseAgent):
    def __init__(self, bbs: BBS):
        super().__init__("technical_agent", bbs)
        assert "technical_calc" in self.config["allowed_skills"], \
            f"{self.name}: technical_calc へのアクセス権がありません"

    def run(self) -> dict:
        print(f"\n[{self.name}] テクニカル指標の計算を開始します...")
        params = self.config["params"]
        tickers = params.get("tickers", {})
        period = params.get("period", "3mo")

        result = _technical_calc_mod.run(tickers=tickers, period=period)

        self.bbs.write(self.name, self.config["output_bbs_key"], result)
        succeeded = [k for k, v in result["tickers"].items() if not v.get("error")]
        print(f"  [{self.name}] 計算完了: {succeeded}")
        return result


# =========================================================
# 4. ManagerAgent
# =========================================================

class ManagerAgent(BaseAgent):
    def __init__(self, bbs: BBS):
        super().__init__("manager_agent", bbs)

    def run(self) -> dict:
        print(f"\n[{self.name}] BBS読み取り → 統合判断を開始します...")

        news_data = self.bbs.read("news_analysis") or {}
        fundamental_data = self.bbs.read("fundamental_analysis") or {}
        technical_data = self.bbs.read("technical_analysis") or {}

        bbs_summary = (
            f"=== NewsAgent出力 ===\n{json.dumps(news_data, ensure_ascii=False, indent=2)}\n\n"
            f"=== FundamentalAgent出力 ===\n{json.dumps(fundamental_data, ensure_ascii=False, indent=2)}\n\n"
            f"=== TechnicalAgent出力 ===\n{json.dumps(technical_data, ensure_ascii=False, indent=2)}"
        )

        system = self.config["system_prompt"]
        prompt = (
            f"{system}\n\n"
            f"以下のBBSデータを統合して売買判断を下してください。\n"
            f"必ず以下のJSON形式のみで出力してください（余分なテキスト不要）:\n"
            f'{{"action":"BUY"|"HOLD"|"SELL","confidence":0-100,'
            f'"rationale":"理由","position_size_pct":0-20,'
            f'"stop_loss_pct":1-8,"target_hold_days":3-20}}\n\n'
            f"=== BBSデータ ===\n{bbs_summary}"
        )

        try:
            raw = self.llm.invoke(prompt).content.strip()
            import re
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            judgment = json.loads(m.group()) if m else {"action": "HOLD", "confidence": 0, "rationale": raw}
        except Exception as e:
            judgment = {
                "action": "HOLD",
                "confidence": 0,
                "rationale": f"判断生成エラー: {e}",
                "position_size_pct": 0,
                "stop_loss_pct": 5,
                "target_hold_days": 5,
            }

        self.bbs.write(self.name, self.config["output_bbs_key"], judgment)
        print(f"  [{self.name}] 判断: {judgment.get('action')} (確信度: {judgment.get('confidence')}%)")
        return judgment


# =========================================================
# 5. ComplianceAgent
# =========================================================

class ComplianceAgent(BaseAgent):
    def __init__(self, bbs: BBS):
        super().__init__("compliance_agent", bbs)
        rules_path = Path(self.config["rules_file"])
        self.rules_text = rules_path.read_text(encoding="utf-8") if rules_path.exists() else "ルールファイルなし"

    def run(self) -> dict:
        print(f"\n[{self.name}] ManagerAgent判断を検閲します...")

        manager_judgment = self.bbs.read("manager_judgment") or {}
        news_data = self.bbs.read("news_analysis") or {}

        system = self.config["system_prompt"]
        prompt = (
            f"{system}\n\n"
            f"=== コンプライアンスルール ===\n{self.rules_text}\n\n"
            f"=== ManagerAgentの判断 ===\n{json.dumps(manager_judgment, ensure_ascii=False, indent=2)}\n\n"
            f"=== NewsAgentの出力（RULE-06検証用） ===\n{json.dumps(news_data, ensure_ascii=False, indent=2)}\n\n"
            f"必ず以下のJSON形式のみで出力してください（余分なテキスト不要）:\n"
            f'{{"compliance_status":"APPROVED"|"MODIFIED"|"REJECTED",'
            f'"violations":[],"final_action":"BUY"|"HOLD"|"SELL",'
            f'"final_position_size_pct":0-20,"compliance_note":"理由"}}'
        )

        try:
            raw = self.llm.invoke(prompt).content.strip()
            import re
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            decision = json.loads(m.group()) if m else {
                "compliance_status": "REJECTED",
                "violations": ["パースエラー"],
                "final_action": "HOLD",
                "final_position_size_pct": 0,
                "compliance_note": raw[:100],
            }
        except Exception as e:
            decision = {
                "compliance_status": "REJECTED",
                "violations": [str(e)],
                "final_action": "HOLD",
                "final_position_size_pct": 0,
                "compliance_note": f"検閲エラーのためHOLDに強制変更: {e}",
            }

        self.bbs.write(self.name, self.config["output_bbs_key"], decision)
        print(f"  [{self.name}] ステータス: {decision.get('compliance_status')} → 最終アクション: {decision.get('final_action')}")
        return decision


# =========================================================
# LINE通知
# =========================================================

LINE_ACCESS_TOKEN = os.getenv(
    "LINE_ACCESS_TOKEN",
    "rLmNKB5qoOYjlQ1W7G46SpD2dhH3uxCNxqHYnyqWKTmPRWGPP0ZpqrfWs8y3MRFXym3ctwIZXlC14eo2LxXjx++Hha4Fgy2RJX1Ii1LCuRuThgkshqMko1DHIgbDrm812uX+2ywiI6vA9GuJiBy3pAdB04t89/1O/w1cDnyilFU=",
)
LINE_USER_ID = os.getenv("LINE_USER_ID", "U266575a29b79da182dfad34f6e879603")


def send_line_message(text: str) -> None:
    try:
        requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
            },
            data=json.dumps({
                "to": LINE_USER_ID,
                "messages": [{"type": "text", "text": text}],
            }),
            timeout=10,
        )
    except Exception as e:
        print(f"  [LINE] 送信失敗: {e}")


def _build_line_report(bbs: BBS, final_decision: dict) -> str:
    news = bbs.read("news_analysis") or {}
    manager = bbs.read("manager_judgment") or {}

    action_emoji = {"BUY": "📈", "SELL": "📉", "HOLD": "⏸"}.get(
        final_decision.get("final_action", "HOLD"), "❓"
    )

    articles_summary = ""
    for a in (news.get("articles") or [])[:3]:
        emoji = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(a.get("sentiment", ""), "❔")
        articles_summary += f"  {emoji} {a['company']}: {a['title'][:30]}...\n"

    return (
        f"🤖 【スイングトレード AIレポート】\n"
        f"{'='*30}\n"
        f"{action_emoji} 最終判断: {final_decision.get('final_action', 'N/A')}\n"
        f"📊 コンプライアンス: {final_decision.get('compliance_status', 'N/A')}\n"
        f"💼 推奨サイズ: {final_decision.get('final_position_size_pct', 0)}%\n"
        f"\n📌 ManagerAgent根拠:\n{manager.get('rationale', 'なし')[:150]}\n"
        f"\n📰 注目ニュース:\n{articles_summary}"
        f"\n⚠️ 違反ルール: {', '.join(final_decision.get('violations') or ['なし'])}\n"
        f"{'='*30}\n"
        f"✏️ {final_decision.get('compliance_note', '')}"
    )


# =========================================================
# オーケストレーション本体
# =========================================================

def orchestrate(notify_line: bool = True) -> dict:
    session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"\n{'='*50}")
    print(f"  スイングトレード AIエージェント起動")
    print(f"  セッション: {session_id}")
    print(f"{'='*50}")

    bbs = BBS(session_id)

    # --- フェーズ1: 情報収集エージェント（独立実行）---
    print("\n[ Phase 1 ] 情報収集エージェント群を起動...")
    NewsAgent(bbs).run()
    FundamentalAgent(bbs).run()
    TechnicalAgent(bbs).run()

    # --- フェーズ2: 統合判断 ---
    print("\n[ Phase 2 ] ManagerAgent が統合判断を実行...")
    ManagerAgent(bbs).run()

    # --- フェーズ3: コンプライアンス検閲 ---
    print("\n[ Phase 3 ] ComplianceAgent が最終検閲を実行...")
    final_decision = ComplianceAgent(bbs).run()

    # --- フェーズ4: BBSダンプ & LINE通知 ---
    print(f"\n{'='*50}")
    print("[ BBS最終状態 ]")
    print(bbs.to_text_summary())

    if notify_line:
        report = _build_line_report(bbs, final_decision)
        print("\n[ LINE通知 ] 送信中...")
        send_line_message(report)
        print("  送信完了。")

    print(f"\n  BBSログ保存先: {bbs.path}")
    print(f"{'='*50}\n")
    return final_decision


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="スイングトレード AIエージェントシステム")
    parser.add_argument("--no-line", action="store_true", help="LINE通知を送らない")
    args = parser.parse_args()

    result = orchestrate(notify_line=not args.no_line)
    print(f"\n最終判断: {result.get('final_action')} ({result.get('compliance_status')})")
