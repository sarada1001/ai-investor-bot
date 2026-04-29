# Skills registry — maps skill names to their run() functions
# Use _ prefix to avoid shadowing submodule names on attribute lookup
from skills.news_monitor import run as _news_monitor_run
from skills.rag_search import run as _rag_search_run
from skills.technical_calc import run as _technical_calc_run
from skills.signal_scorer import run as _signal_scorer_run
from skills.alpaca_trade import run as _alpaca_trade_run

REGISTRY = {
    "news_monitor": _news_monitor_run,
    "rag_search": _rag_search_run,
    "technical_calc": _technical_calc_run,
    "signal_scorer": _signal_scorer_run,
    "alpaca_trade": _alpaca_trade_run,
}
