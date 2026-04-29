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
import skills.portfolio_tracker as _portfolio_mod
import skills.signal_scorer as _signal_scorer_mod
import skills.alpaca_trade as _alpaca_mod

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
        assert "signal_scorer" in self.config["allowed_skills"], \
            f"{self.name}: signal_scorer へのアクセス権がありません"

    def run(self) -> dict:
        print(f"\n[{self.name}] BBS読み取り → 重み付きスコアリング判断を開始します...")

        news_data = self.bbs.read("news_analysis") or {}
        fundamental_data = self.bbs.read("fundamental_analysis") or {}
        technical_data = self.bbs.read("technical_analysis") or {}

        # --- ファンダメンタルシグナルを1回だけLLMで抽出（全銘柄共通）---
        print(f"  [{self.name}] ファンダメンタルシグナルを抽出中...")
        fa_signal, fa_reason = _signal_scorer_mod.extract_fundamental_signal(fundamental_data, self.llm)

        # --- 全銘柄をスコアリングして最良機会を選択 ---
        tickers = technical_data.get("tickers", {})
        best_result: dict | None = None
        best_company = ""
        best_ticker = ""

        for company, ticker_info in tickers.items():
            if ticker_info.get("error"):
                continue
            result = _signal_scorer_mod.run(
                news_data=news_data,
                fundamental_data=fundamental_data,
                technical_data=technical_data,
                target_company=company,
                fundamental_signal=fa_signal,
                fundamental_reason=fa_reason,
            )
            if best_result is None or abs(result["score"]) > abs(best_result["score"]):
                best_result = result
                best_company = company
                best_ticker = ticker_info.get("ticker", "")

        if best_result is None:
            judgment = {
                "action": "HOLD", "target_company": "", "target_ticker": "",
                "confidence": 0, "rationale": "テクニカルデータ取得失敗のためHOLD",
                "position_size_pct": 0, "stop_loss_pct": 5, "target_hold_days": 5,
            }
            self.bbs.write(self.name, self.config["output_bbs_key"], judgment)
            return judgment

        # --- LLMで根拠テキストを生成（スコアの説明のみ）---
        score_info = best_result
        rationale_prompt = (
            f"スイングトレード分析結果を投資家向けに100文字以内で要約してください。\n\n"
            f"対象銘柄: {best_company}\n"
            f"判断: {score_info['action']} (加重スコア: {score_info['score']:.3f})\n"
            f"ニュース({score_info['weights']['news']:.0%}): "
            f"{score_info['signals']['news']:+.2f} — {score_info['signal_reasons']['news']}\n"
            f"ファンダメンタルズ({score_info['weights']['fundamental']:.0%}): "
            f"{score_info['signals']['fundamental']:+.2f} — {score_info['signal_reasons']['fundamental']}\n"
            f"テクニカル({score_info['weights']['technical']:.0%}): "
            f"{score_info['signals']['technical']:+.2f} — {score_info['signal_reasons']['technical']}\n"
            f"ウェイト調整: {score_info['weight_reason']}"
        )
        try:
            rationale = self.llm.invoke(rationale_prompt).content.strip()[:200]
        except Exception as e:
            rationale = f"{best_company}: スコア{score_info['score']:.3f}により{score_info['action']} (LLMエラー: {e})"

        # --- スコアから派生パラメータを計算 ---
        score_abs = abs(score_info["score"])
        position_size_pct = max(5, min(20, round(score_abs * 20)))
        stop_loss_pct = max(3, min(8, round(8 - score_abs * 5)))
        target_hold_days = max(3, min(20, round(5 + score_abs * 15)))

        judgment = {
            "action": score_info["action"],
            "target_company": best_company,
            "target_ticker": best_ticker,
            "confidence": score_info["confidence"],
            "rationale": rationale,
            "position_size_pct": position_size_pct,
            "stop_loss_pct": stop_loss_pct,
            "target_hold_days": target_hold_days,
            "score_breakdown": score_info,
        }

        self.bbs.write(self.name, self.config["output_bbs_key"], judgment)
        print(
            f"  [{self.name}] 判断: {judgment['action']} "
            f"(スコア: {score_info['score']:+.3f}, 確信度: {judgment['confidence']}%)"
            f" → {best_company}"
        )
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
        portfolio_data = self.bbs.read("portfolio_state") or {}

        target_company = manager_judgment.get("target_company", "")
        recent_buy_flag = _portfolio_mod.recent_buy_within_days(
            {"recent_decisions": portfolio_data.get("recent_decisions_last10", [])},
            company=target_company,
        ) if target_company else False
        holding_cnt = portfolio_data.get("holding_count", 0)
        total_pct = portfolio_data.get("total_position_pct", 0.0)
        new_size = manager_judgment.get("position_size_pct", 0)

        rule02_note = (
            f"直近3日以内にBUY記録あり → RULE-02違反のためREJECT必須"
            if recent_buy_flag
            else "直近3日以内のBUY記録なし → 問題なし"
        )
        rule07_note = (
            f"保有銘柄数 {holding_cnt}/4、追加後総ポジション {total_pct + new_size:.1f}% "
            f"({'60%超 → REJECT必須' if total_pct + new_size > 60 or holding_cnt >= 4 else '上限内'})"
        )

        system = self.config["system_prompt"]
        prompt = (
            f"{system}\n\n"
            f"=== コンプライアンスルール ===\n{self.rules_text}\n\n"
            f"=== ManagerAgentの判断 ===\n{json.dumps(manager_judgment, ensure_ascii=False, indent=2)}\n\n"
            f"=== NewsAgentの出力（RULE-06検証用） ===\n{json.dumps(news_data, ensure_ascii=False, indent=2)}\n\n"
            f"=== ポートフォリオ現状（RULE-02/07検証用） ===\n"
            f"{json.dumps(portfolio_data, ensure_ascii=False, indent=2)}\n\n"
            f"[RULE-02チェック結果] 対象銘柄「{target_company}」: {rule02_note}\n"
            f"[RULE-07チェック結果] {rule07_note}\n\n"
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
    alpaca_order = bbs.read("alpaca_order")

    action_emoji = {"BUY": "📈", "SELL": "📉", "HOLD": "⏸"}.get(
        final_decision.get("final_action", "HOLD"), "❓"
    )

    articles_summary = ""
    for a in (news.get("articles") or [])[:3]:
        emoji = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(a.get("sentiment", ""), "❔")
        articles_summary += f"  {emoji} {a['company']}: {a['title'][:30]}...\n"

    if alpaca_order:
        if alpaca_order.get("error"):
            alpaca_line = f"\n⚠️ Alpaca発注エラー: {str(alpaca_order['error'])[:60]}"
        else:
            alpaca_line = (
                f"\n🏦 Alpaca Paper発注:\n"
                f"  {alpaca_order.get('symbol')} {str(alpaca_order.get('side','')).upper()} "
                f"{alpaca_order.get('qty')}株\n"
                f"  OrderID: {str(alpaca_order.get('order_id',''))[:16]}..."
            )
    else:
        alpaca_line = "\n🏦 Alpaca: 発注なし（日本株 or HOLD）"

    return (
        f"🤖 【スイングトレード AIレポート】\n"
        f"{'='*30}\n"
        f"{action_emoji} 最終判断: {final_decision.get('final_action', 'N/A')}\n"
        f"📊 コンプライアンス: {final_decision.get('compliance_status', 'N/A')}\n"
        f"💼 推奨サイズ: {final_decision.get('final_position_size_pct', 0)}%\n"
        f"\n📌 ManagerAgent根拠:\n{manager.get('rationale', 'なし')[:150]}\n"
        f"\n📰 注目ニュース:\n{articles_summary}"
        f"{alpaca_line}\n"
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

    # --- ポートフォリオ状態を読み込み BBS に書き込む ---
    portfolio_state = _portfolio_mod.load()
    portfolio_summary = _portfolio_mod.get_summary(portfolio_state)
    bbs.write("Orchestrator", "portfolio_state", portfolio_summary)
    print(f"  [Portfolio] 保有銘柄数: {portfolio_summary['holding_count']}, "
          f"総ポジション: {portfolio_summary['total_position_pct']}%")

    # --- フェーズ1: 情報収集エージェント（独立実行）---
    print("\n[ Phase 1 ] 情報収集エージェント群を起動...")
    NewsAgent(bbs).run()
    FundamentalAgent(bbs).run()
    TechnicalAgent(bbs).run()

    # --- フェーズ2: 統合判断 ---
    print("\n[ Phase 2 ] ManagerAgent が統合判断を実行...")
    manager_judgment = ManagerAgent(bbs).run()

    # --- フェーズ3: コンプライアンス検閲 ---
    print("\n[ Phase 3 ] ComplianceAgent が最終検閲を実行...")
    final_decision = ComplianceAgent(bbs).run()

    # --- ポートフォリオ状態を更新して永続化 ---
    target_company = manager_judgment.get("target_company", "") if isinstance(manager_judgment, dict) else ""
    target_ticker = manager_judgment.get("target_ticker", "") if isinstance(manager_judgment, dict) else ""
    final_action = final_decision.get("final_action", "HOLD")
    final_position_pct = final_decision.get("final_position_size_pct", 0)

    current_price: float | None = None
    if target_company and final_action in ("BUY", "SELL"):
        technical_data = bbs.read("technical_analysis") or {}
        ticker_data = technical_data.get("tickers", {}).get(target_company, {})
        current_price = (
            ticker_data.get("ma25", {}).get("latest_price")
            if not ticker_data.get("error")
            else None
        )
        _portfolio_mod.apply_decision(
            portfolio_state,
            company=target_company,
            ticker=target_ticker,
            action=final_action,
            position_size_pct=final_position_pct,
            current_price=current_price,
        )
        _portfolio_mod.save(portfolio_state)
        print(f"  [Portfolio] 状態を更新: {target_company} → {final_action} "
              f"({final_position_pct}%, 価格: {current_price})")

    # --- フェーズ3.5: Alpaca Paper Trading 発注 ---
    # 米国株（ティッカーに "." を含まない）かつ ComplianceAgent 承認済みの場合のみ発注
    alpaca_order: dict | None = None
    _is_us_ticker = bool(target_ticker and "." not in target_ticker)
    _compliance_ok = final_decision.get("compliance_status") in ("APPROVED", "MODIFIED")

    if final_action in ("BUY", "SELL") and target_ticker and _compliance_ok:
        if _is_us_ticker:
            print(f"\n[ Phase 3.5 ] Alpaca Paper Trading 発注...")
            try:
                account_info = _alpaca_mod.get_account_info()
                buying_power = account_info["buying_power"]
                if current_price and current_price > 0 and final_position_pct > 0:
                    qty = max(1.0, round(buying_power * (final_position_pct / 100) / current_price, 4))
                else:
                    qty = 1.0
                alpaca_order = _alpaca_mod.place_market_order(target_ticker, qty, final_action.lower())
                bbs.write("Orchestrator", "alpaca_order", alpaca_order)
                print(f"  [Alpaca] 発注完了: {target_ticker} {final_action} {qty}株 "
                      f"→ OrderID: {alpaca_order['order_id']}")
            except Exception as e:
                alpaca_order = {"error": str(e), "ticker": target_ticker}
                bbs.write("Orchestrator", "alpaca_order", alpaca_order)
                print(f"  [Alpaca] 発注失敗: {e}")
        else:
            print(f"\n[ Phase 3.5 ] Alpaca: {target_ticker} は日本株のためスキップ（portfolio_state のみ更新）")

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
