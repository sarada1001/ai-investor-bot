# Skills registry — maps skill names to their run() functions
from skills.news_monitor import run as news_monitor
from skills.rag_search import run as rag_search
from skills.technical_calc import run as technical_calc
from skills.signal_scorer import run as signal_scorer
from skills.alpaca_trade import run as alpaca_trade

REGISTRY = {
    "news_monitor": news_monitor,
    "rag_search": rag_search,
    "technical_calc": technical_calc,
    "signal_scorer": signal_scorer,
    "alpaca_trade": alpaca_trade,
}
