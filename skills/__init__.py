# Skills registry — maps skill names to their run() functions
# Use _ prefix to avoid shadowing submodule names on attribute lookup
from skills.news_monitor import run as _news_monitor_run
from skills.rag_search import run as _rag_search_run
from skills.technical_calc import run as _technical_calc_run
from skills.signal_scorer import run as _signal_scorer_run
from skills.alpaca_trade import run as _alpaca_trade_run
from skills.edgar_fetcher import run as _edgar_fetcher_run
from skills.macro_monitor import run as _macro_monitor_run
from skills.social_monitor import run as _social_monitor_run
from skills.risk_calculator import run as _risk_calculator_run
from skills.portfolio_monitor import run as _portfolio_monitor_run

REGISTRY = {
    "news_monitor":      _news_monitor_run,
    "rag_search":        _rag_search_run,
    "technical_calc":    _technical_calc_run,
    "signal_scorer":     _signal_scorer_run,
    "alpaca_trade":      _alpaca_trade_run,
    "edgar_fetcher":     _edgar_fetcher_run,
    "macro_monitor":     _macro_monitor_run,
    "social_monitor":    _social_monitor_run,
    "risk_calculator":   _risk_calculator_run,
    "portfolio_monitor": _portfolio_monitor_run,
}
