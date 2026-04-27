# Skills registry — maps skill names to their run() functions
from skills.news_monitor import run as news_monitor
from skills.rag_search import run as rag_search
from skills.technical_calc import run as technical_calc

REGISTRY = {
    "news_monitor": news_monitor,
    "rag_search": rag_search,
    "technical_calc": technical_calc,
}
