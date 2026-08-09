#!/usr/bin/env python3
"""
scripts/preflight_check.py — ペーパートレード本番実行前のプリフライトチェック

使い方:
    python scripts/preflight_check.py
    python scripts/preflight_check.py --verbose

全チェック PASS → READY FOR PAPER TRADING と表示して exit(0)
CRITICAL 失敗あり → exit(1)
WARNING のみ  → exit(0) （続行可能）
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_W = 60

# ────────────────────────────────────────────────────────────
# 結果型
# ────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    name:     str
    ok:       bool          # True = PASS / WARN, False = FAIL
    level:    str           # "OK" | "WARN" | "FAIL"
    detail:   str
    critical: bool = False  # True = 失敗で全体 FAIL


@dataclass
class PreflightReport:
    checks: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.checks.append(result)

    @property
    def failed(self) -> list[CheckResult]:
        return [c for c in self.checks if c.level == "FAIL"]

    @property
    def critical_failed(self) -> list[CheckResult]:
        return [c for c in self.checks if c.level == "FAIL" and c.critical]

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if c.level == "WARN"]

    @property
    def ready(self) -> bool:
        return len(self.critical_failed) == 0


# ────────────────────────────────────────────────────────────
# 各チェック関数
# ────────────────────────────────────────────────────────────

def check_alpaca(verbose: bool) -> CheckResult:
    try:
        from tools.alpaca_client import AlpacaClient, _PAPER
        client = AlpacaClient()
        acc    = client.get_account()
        if "error" in acc:
            return CheckResult("Alpaca Paper API", False, "FAIL",
                               f"口座取得エラー: {acc['error']}", critical=True)
        eq    = acc.get("equity", 0)
        cash  = acc.get("cash", 0)
        pos   = client.get_positions()
        _, mkt = client.is_market_open()
        paper_str = "PAPER" if _PAPER else "LIVE ⚠️"
        detail = (
            f"mode={paper_str}  equity=${eq:,.0f}  cash=${cash:,.0f}  "
            f"positions={len(pos)}件  market={mkt[:20]}"
        )
        if not _PAPER:
            return CheckResult("Alpaca API", False, "FAIL",
                               "ALPACA_PAPER_TRADING=false です。ペーパーモードで実行してください。",
                               critical=True)
        return CheckResult("Alpaca Paper API", True, "OK", detail, critical=True)
    except Exception as e:
        return CheckResult("Alpaca Paper API", False, "FAIL", str(e), critical=True)


def check_gemini(verbose: bool) -> CheckResult:
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        from skills.llm_factory import get_gemini_model
        key = os.getenv("GOOGLE_API_KEY", "")
        if not key:
            return CheckResult("Google Gemini", False, "FAIL", "GOOGLE_API_KEY 未設定", critical=True)
        # get_llm() 経由にしないのは意図的。この検査の目的は「GOOGLE_API_KEY が
        # Gemini API で実際に通るか」であり、Ollama が稼働していると get_llm() は
        # Gemini を一切叩かず検査が空振りする。モデル名だけを factory から取る。
        model = get_gemini_model()
        llm = ChatGoogleGenerativeAI(model=model, google_api_key=key)
        res = llm.invoke("Reply with OK only.")
        return CheckResult("Google Gemini", True, "OK",
                           f"model={model}  応答: {str(res.content)[:30]}", critical=True)
    except Exception as e:
        return CheckResult("Google Gemini", False, "FAIL", str(e)[:80], critical=True)


def check_ollama(verbose: bool) -> CheckResult:
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=3) as r:
            d      = json.loads(r.read())
            models = [m["name"] for m in d.get("models", [])]
        if not models:
            return CheckResult("Ollama", False, "WARN",
                               "接続OK だがモデルが空 — CriticAgent はフォールバック動作")
        return CheckResult("Ollama", True, "OK",
                           f"models={models[:3]}")
    except Exception as e:
        return CheckResult("Ollama", False, "WARN",
                           f"未接続 ({e.__class__.__name__}) — CriticAgent はフォールバック動作")


def check_line(verbose: bool) -> CheckResult:
    try:
        token = os.getenv("LINE_ACCESS_TOKEN", "")
        if not token:
            return CheckResult("LINE API", False, "WARN", "LINE_ACCESS_TOKEN 未設定 — 通知なし")
        req = urllib.request.Request(
            "https://api.line.me/v2/bot/info",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read())
        return CheckResult("LINE API", True, "OK", f"bot={d.get('displayName','?')}")
    except Exception as e:
        return CheckResult("LINE API", False, "WARN", str(e)[:60])


def check_yfinance(verbose: bool) -> CheckResult:
    try:
        import yfinance as yf
        df = yf.download("SPY", period="3d", auto_adjust=True, progress=False)
        if df.empty:
            return CheckResult("yfinance", False, "FAIL", "SPY データ取得失敗", critical=True)
        close = df["Close"]
        if hasattr(close, "columns"):
            close = close.iloc[:, 0]
        val = float(close.iloc[-1])
        return CheckResult("yfinance", True, "OK",
                           f"SPY 最終終値=${val:.2f}  {len(df)}行", critical=True)
    except Exception as e:
        return CheckResult("yfinance", False, "FAIL", str(e)[:60], critical=True)


def check_chromadb(verbose: bool) -> CheckResult:
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(ROOT / "data" / "chroma"))
        cols   = client.list_collections()
        if not cols:
            return CheckResult("ChromaDB", True, "WARN",
                               "コレクションが空 — CriticAgent の過去ルール参照なし（初回は正常）")
        total_docs = sum(c.count() for c in cols)
        return CheckResult("ChromaDB", True, "OK",
                           f"{len(cols)} コレクション  {total_docs} ドキュメント")
    except Exception as e:
        return CheckResult("ChromaDB", False, "WARN", str(e)[:60])


def check_circuit_breaker(verbose: bool) -> CheckResult:
    state_path = ROOT / "data" / "circuit_breaker_state.json"
    if not state_path.exists():
        return CheckResult("CircuitBreaker", True, "OK", "未初期化（初回起動で自動作成）")
    try:
        d      = json.loads(state_path.read_text())
        status = d.get("status", "?")
        if status == "HARD_TRIP":
            return CheckResult("CircuitBreaker", False, "FAIL",
                               f"HARD_TRIP 中 — manual_reset(level='hard') が必要: {d.get('trip_reason','')[:50]}",
                               critical=True)
        if status == "SOFT_TRIP":
            return CheckResult("CircuitBreaker", False, "WARN",
                               f"SOFT_TRIP 中 — 本日の新規 BUY は停止 (明日自動解除): {d.get('trip_reason','')[:40]}")
        return CheckResult("CircuitBreaker", True, "OK",
                           f"OPEN  日次{d.get('daily_pnl_pct', 0)*100:+.2f}%  "
                           f"高値比{d.get('total_drawdown_pct', 0)*100:+.2f}%")
    except Exception as e:
        return CheckResult("CircuitBreaker", False, "WARN", str(e)[:60])


def check_trade_guard(verbose: bool) -> CheckResult:
    state_path  = ROOT / "data" / "trade_guard_state.json"
    config_path = ROOT / "data" / "trade_guards.json"
    try:
        cfg = json.loads(config_path.read_text()) if config_path.exists() else {}
        max_daily = cfg.get("max_daily_buys", 3)
        if not state_path.exists():
            return CheckResult("TradeGuard", True, "OK",
                               f"未初期化（初回起動で自動作成）  上限={max_daily}回/日")
        d     = json.loads(state_path.read_text())
        count = d.get("daily_buy_count", 0)
        today = str(date.today())
        reset = d.get("last_reset_date", "")
        if reset != today:
            count = 0  # 翌日リセット済み扱い
        icon = "⚠️" if count >= max_daily else "OK"
        detail = f"本日 BUY {count}/{max_daily} 回"
        if count >= max_daily:
            return CheckResult("TradeGuard", False, "WARN",
                               f"{detail} — 本日の BUY 上限に到達済み")
        return CheckResult("TradeGuard", True, "OK", detail)
    except Exception as e:
        return CheckResult("TradeGuard", False, "WARN", str(e)[:60])


def check_live_gate(verbose: bool) -> CheckResult:
    try:
        from tools.live_trading_gate import LiveTradingGate, _IS_PAPER
        if _IS_PAPER:
            return CheckResult("LiveTradingGate", True, "OK",
                               "PAPER モード — ゲート認証不要")
        result = LiveTradingGate().check()
        if result.allowed:
            return CheckResult("LiveTradingGate", True, "OK",
                               f"LIVE 認証済み  期限={result.expires_at[:16] if result.expires_at else 'N/A'}")
        return CheckResult("LiveTradingGate", False, "FAIL",
                           result.reason.splitlines()[0], critical=True)
    except Exception as e:
        return CheckResult("LiveTradingGate", False, "WARN", str(e)[:60])


def check_agent_status(verbose: bool) -> CheckResult:
    path = ROOT / "data" / "agent_status.json"
    if not path.exists():
        return CheckResult("Agent Status", False, "WARN", "agent_status.json が存在しません — 評価未実施")
    try:
        d         = json.loads(path.read_text())
        active    = [k for k, v in d.items() if v.get("status") == "ACTIVE"]
        suspended = [k for k, v in d.items() if v.get("status") == "SUSPENDED"]
        detail    = f"{len(active)} ACTIVE"
        if suspended:
            detail += f" / {len(suspended)} SUSPENDED: {', '.join(suspended)}"
        if not active:
            return CheckResult("Agent Status", False, "FAIL",
                               "全エージェントが SUSPENDED です", critical=True)
        return CheckResult("Agent Status", True, "OK", detail)
    except Exception as e:
        return CheckResult("Agent Status", False, "WARN", str(e)[:60])


def check_portfolio(verbose: bool) -> CheckResult:
    path = ROOT / "data" / "portfolio.json"
    if not path.exists():
        return CheckResult("Portfolio JSON", False, "WARN", "portfolio.json が存在しません")
    try:
        d    = json.loads(path.read_text())
        open_pos = [p for p in d.get("positions", []) if p.get("status") == "OPEN"]
        return CheckResult("Portfolio JSON", True, "OK",
                           f"OPEN ポジション {len(open_pos)} 件  "
                           f"updated_at={d.get('updated_at','?')[:16]}")
    except Exception as e:
        return CheckResult("Portfolio JSON", False, "WARN", str(e)[:60])


# ────────────────────────────────────────────────────────────
# メイン
# ────────────────────────────────────────────────────────────

def run_preflight(verbose: bool = False) -> PreflightReport:
    report = PreflightReport()

    checks = [
        ("Alpaca Paper API", check_alpaca),
        ("Google Gemini",    check_gemini),
        ("Ollama",           check_ollama),
        ("LINE API",         check_line),
        ("yfinance",         check_yfinance),
        ("ChromaDB",         check_chromadb),
        ("CircuitBreaker",   check_circuit_breaker),
        ("TradeGuard",       check_trade_guard),
        ("LiveTradingGate",  check_live_gate),
        ("Agent Status",     check_agent_status),
        ("Portfolio JSON",   check_portfolio),
    ]

    print(f"\n{'═' * _W}")
    print("  ECC Paper Trading — Pre-Flight Check")
    print(f"{'═' * _W}\n")

    for label, fn in checks:
        result = fn(verbose)
        report.add(result)
        icon = {"OK": "✅", "WARN": "⚠️ ", "FAIL": "❌"}[result.level]
        crit = " [CRITICAL]" if result.critical and result.level == "FAIL" else ""
        name_col = f"{icon} {result.name:<22}"
        print(f"  {name_col} {result.detail}{crit}")

    print(f"\n{'─' * _W}")
    if report.critical_failed:
        print(f"  ❌ BLOCKED — {len(report.critical_failed)} 件の CRITICAL エラーがあります\n")
        for c in report.critical_failed:
            print(f"     • {c.name}: {c.detail}")
        print()
    elif report.warnings:
        print(f"  ⚠️  READY WITH WARNINGS — {len(report.warnings)} 件の警告 (続行可能)\n")
        for w in report.warnings:
            print(f"     • {w.name}: {w.detail}")
        print()
    else:
        print("  ✅ READY FOR PAPER TRADING\n")

    if report.ready:
        print("  実行コマンド例:")
        print("    .venv/bin/python main.py --screen --notify-line")
        print("    .venv/bin/python main.py --daemon --screen --notify-line")
    print(f"{'═' * _W}\n")

    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ECC プリフライトチェック")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    report = run_preflight(verbose=args.verbose)
    sys.exit(0 if report.ready else 1)
