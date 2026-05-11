"""engine/display.py — ターミナル表示ヘルパー"""

from __future__ import annotations

from engine.constants import _W


def _sep() -> None:
    print(f"│  {'┄' * (_W - 4)}")


def _log(msg: str) -> None:
    print(f"│  {msg}")


def _phase_header(tag: str, name: str) -> None:
    title = f" {tag}: {name} "
    line  = title.center(_W - 2, "─")
    print(f"\n┌{line}┐")


def _phase_footer() -> None:
    print(f"└{'─' * _W}┘")


def _stage_header(n: int, title: str) -> None:
    label = f"  ◆ Stage {n}: {title}"
    print(f"\n{'━' * (_W + 2)}")
    print(label)
    print(f"{'━' * (_W + 2)}")


def _mock_banner(sub: str = "") -> None:
    bar = "█" * (_W + 2)
    msg = "⚠️  [MOCK MODE]  トークン消費0でテスト実行中  ⚠️"
    print(f"\n{bar}")
    print(f"  {msg}")
    if sub:
        print(f"  {sub}")
    print(f"{bar}\n")


def _hybrid_banner(sub: str = "") -> None:
    bar = "▓" * (_W + 2)
    msg = "🔄  [HYBRID MODE]  リアル市場データ / 発注スキップ  🔄"
    print(f"\n{bar}")
    print(f"  {msg}")
    if sub:
        print(f"  {sub}")
    print(f"{bar}\n")


def _live_gate_banner(dry_run: bool = False) -> None:
    from tools.live_trading_gate import LiveTradingGate, _IS_PAPER
    if _IS_PAPER or dry_run:
        return
    result = LiveTradingGate().check()
    bar    = "🔴" * ((_W + 2) // 2)
    if result.allowed:
        print(f"\n{bar}")
        print(f"  💰  [LIVE TRADING]  実弾取引モード  💰")
        print(f"  認証期限 : {result.expires_at[:16] if result.expires_at else 'N/A'}")
        print(f"  API Key  : ****{result.key_suffix}")
        print(f"{bar}\n")
    else:
        print(f"\n{bar}")
        print(f"  🚫  [LIVE TRADING GATE]  発注ブロック中  🚫")
        for line in result.reason.splitlines():
            print(f"  {line}")
        print(f"{bar}\n")


def _main_header(ticker: str, session_id: str) -> None:
    print(f"\n╔{'═' * _W}╗")
    print(f"║  ECC スイングトレード自律エンジン  [{ticker}]".ljust(_W + 1) + "║")
    print(f"║  セッション: {session_id}".ljust(_W + 1) + "║")
    print(f"╚{'═' * _W}╝")


def _decision_box(lines: list[str]) -> None:
    print(f"\n╔{'═' * _W}╗")
    for line in lines:
        print(f"║  {line}".ljust(_W + 1) + "║")
    print(f"╚{'═' * _W}╝")
