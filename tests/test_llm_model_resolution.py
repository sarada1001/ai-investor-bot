"""tests/test_llm_model_resolution.py — Gemini モデル名の解決経路を固定するテスト

## なぜこのファイルが必要か

`gemini-2.0-flash` が Google 側で廃止された後も、ExitAgent・rag_search・
preflight_check がモデル名を引数デフォルトや明示指定で直書きしていたため、
本番 `.env` の `GEMINI_MODEL=gemini-2.5-flash` が**上書きされ続けていた**。

結果、ExitAgent の thesis 判定（購入理由がニュースで否定されたかの LLM 判断）が
毎日全保有銘柄で `429 RESOURCE_EXHAUSTED (limit: 0)` になり、半年近く
ルールベースフォールバックのみで運用されていた。`limit: 0` は枠切れではなく
「廃止モデルには割り当てが存在しない」の意味なので、リトライでは永久に回復しない。

このテストは以下の2点を固定する:

1. **伝播**: `.env` の `GEMINI_MODEL` を変えると、Gemini を使う全経路が
   実際にその値でクライアントを生成すること。
2. **不在**: `skills/llm_factory.py` 以外の Python コードに Gemini の
   モデル名リテラルが存在しないこと（コメント・docstring は対象外）。

2 が壊れた時点で 1 は無意味になるため、両方が必要。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent

# テスト用のダミーモデル名。実在しないので、これが観測できたということは
# 「実際に .env の値が使われた」ことの証明になる。
_SENTINEL_MODEL = "gemini-sentinel-for-test"

# モデル名の直書きを検出する正規表現（gemini-1.5 / 2.0 / 2.5 / 3 ... を広く捕捉）。
_MODEL_LITERAL_RE = re.compile(r"gemini-\d")

# モデル名リテラルを持ってよい唯一のファイル。
_MODEL_NAME_OWNER = "skills/llm_factory.py"

# 静的スキャンの対象外。仮想環境・キャッシュ・テスト自身（意図的に
# パターンを含む）を除く。
_SCAN_EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".pytest_cache", "build", "dist",
}


@pytest.fixture
def gemini_env(monkeypatch):
    """Gemini 経路を強制し、モデル名をセンチネルに差し替える。

    `load_dotenv(override=False)` は既存の os.environ を上書きしないため、
    monkeypatch.setenv した値が .env より優先される。
    """
    monkeypatch.setenv("GEMINI_MODEL", _SENTINEL_MODEL)
    monkeypatch.setenv("FORCE_GEMINI", "true")
    monkeypatch.setenv("DISABLE_GEMINI", "false")
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy-key-for-test")


@pytest.fixture
def captured_gemini(monkeypatch):
    """ChatGoogleGenerativeAI を差し替え、生成時の model 引数を記録する。

    各呼び出し元は関数内で `from langchain_google_genai import ...` するため、
    元モジュールの属性を張り替えれば全経路に効く。
    """
    genai = pytest.importorskip("langchain_google_genai")
    captured: dict = {}

    class _StubChat:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def invoke(self, _prompt):
            class _Resp:
                content = '{"critic_decision": "APPROVE", ' \
                          '"revised_action": "HOLD", "critique_reason": "stub"}'
            return _Resp()

    monkeypatch.setattr(genai, "ChatGoogleGenerativeAI", _StubChat)
    return captured


# ──────────────────────────────────────────────
# 1. 伝播: .env の GEMINI_MODEL が全経路に届くか
# ──────────────────────────────────────────────

class TestModelPropagation:
    def test_get_gemini_model_reads_env(self, gemini_env):
        from skills.llm_factory import get_gemini_model
        assert get_gemini_model() == _SENTINEL_MODEL

    def test_fallback_is_not_a_retired_model(self, monkeypatch):
        """GEMINI_MODEL 未設定時の既定値が廃止モデルでないこと。

        本番 .env は GEMINI_MODEL を設定しているが、開発機や新規セットアップでは
        この既定値が使われる。ここが廃止モデルだと同じ事故が再発する。
        """
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        from skills.llm_factory import get_gemini_model
        assert get_gemini_model() != "gemini-2.0-flash"

    def test_get_llm_uses_env_model(self, gemini_env, captured_gemini):
        from skills.llm_factory import get_llm
        _, source = get_llm()
        assert source == "gemini"
        assert captured_gemini["model"] == _SENTINEL_MODEL

    def test_exit_agent_uses_env_model(self, gemini_env, captured_gemini):
        """ExitAgent が引数デフォルトで .env を上書きしないこと（本件の直接原因）。"""
        from agents.exit_agent import ExitAgent
        ExitAgent(bbs=None)
        assert captured_gemini["model"] == _SENTINEL_MODEL

    def test_exit_agent_explicit_model_still_wins(self, gemini_env, captured_gemini):
        """明示指定の注入口（テスト・調査用）は残っていること。"""
        from agents.exit_agent import ExitAgent
        ExitAgent(bbs=None, llm_model="explicit-model")
        assert captured_gemini["model"] == "explicit-model"

    def test_critic_agent_uses_env_model(self, gemini_env, captured_gemini):
        """CriticAgent の Gemini フォールバックが .env のモデルを使うこと。"""
        from tools.critic_agent import CriticAgent
        CriticAgent()._call_gemini("test prompt")
        assert captured_gemini["model"] == _SENTINEL_MODEL

    def test_preflight_check_uses_env_model(self, gemini_env, captured_gemini):
        """preflight は Gemini API 疎通の検査なので直接呼びのまま、モデル名だけ factory 由来。"""
        from scripts.preflight_check import check_gemini
        result = check_gemini(verbose=False)
        assert captured_gemini["model"] == _SENTINEL_MODEL
        assert _SENTINEL_MODEL in result.detail

    def test_server_librarian_uses_env_model(self, gemini_env, monkeypatch):
        """server_librarian は REST を直接叩くため、URL 生成を検証する。"""
        import server_librarian

        captured_url: dict = {}

        def _fake_post(url, **kwargs):
            captured_url["url"] = url
            raise RuntimeError("no network in tests")  # call_gemini は握って "" を返す

        monkeypatch.setattr(server_librarian.requests, "post", _fake_post)
        server_librarian.call_gemini("test prompt")
        assert f"/models/{_SENTINEL_MODEL}:generateContent" in captured_url["url"]


# ──────────────────────────────────────────────
# 2. 不在: モデル名リテラルが llm_factory の外に無いか
# ──────────────────────────────────────────────

def _string_literals(path: Path) -> list[str]:
    """docstring を除いた文字列リテラルを返す。

    コメントは AST に現れないため自動的に対象外になる。docstring は
    Constant ノードとして現れるので、各スコープの先頭文にあるものを除く。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))

    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            docstrings.add(id(body[0].value))

    return [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in docstrings
    ]


def _python_files() -> list[Path]:
    return [
        p for p in PROJECT_ROOT.rglob("*.py")
        if not (_SCAN_EXCLUDE_DIRS & set(p.relative_to(PROJECT_ROOT).parts))
        and p.resolve() != Path(__file__).resolve()
    ]


def test_no_hardcoded_gemini_model_outside_llm_factory():
    """モデル名を知っているのは llm_factory だけ、という状態を維持する。

    これが破られると、次にモデルが廃止された際に .env を直しても反映されず、
    再び無言の失敗（429 limit: 0 → 安全側フォールバック）に戻る。
    """
    offenders: list[str] = []
    for path in _python_files():
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel == _MODEL_NAME_OWNER:
            continue
        for literal in _string_literals(path):
            if _MODEL_LITERAL_RE.search(literal):
                offenders.append(f"{rel}: {literal!r}")

    assert not offenders, (
        "Gemini のモデル名が直書きされています。"
        f" skills/llm_factory.get_gemini_model() から取得してください:\n"
        + "\n".join(offenders)
    )
