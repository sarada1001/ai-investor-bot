#!/usr/bin/env python3
"""
scripts/lint_wiki.py
Wiki全体のヘルスチェック。リンク切れ・孤児ページ・矛盾・鮮度の4項目を検査する。
"""
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
WIKI_ROOT    = PROJECT_ROOT / "data" / "knowledge_base" / "wiki"
LOG_DIR      = PROJECT_ROOT / "data" / "knowledge_base" / "obsidian_logs"

WIKI_LINK_RE    = re.compile(r"\[\[([^\]|#]+?)(?:[|#][^\]]*)?\]\]")
FRONTMATTER_RE  = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
STALE_DAYS      = 7

RESET  = "\033[0m"
RED    = "\033[31m"
YELLOW = "\033[33m"
GREEN  = "\033[32m"
BOLD   = "\033[1m"


def c(color: str, text: str) -> str:
    return f"{color}{text}{RESET}"


def parse_frontmatter(text: str) -> dict:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    result = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip().strip('"')
    return result


def collect_wiki_files() -> dict[str, Path]:
    """wiki/ 内の全 .md を {stem: Path} で返す（サブディレクトリを含む相対stem）。"""
    files = {}
    for p in WIKI_ROOT.rglob("*.md"):
        rel = p.relative_to(WIKI_ROOT)
        # キーは拡張子なし相対パス（例: "tickers/AAPL"）
        files[str(rel.with_suffix(""))] = p
        # ファイル名単体でも引ける（Obsidianの短縮リンク互換）
        files[p.stem] = p
    return files


def collect_links(text: str) -> list[str]:
    return WIKI_LINK_RE.findall(text)


# ─────────────────────────────────────────────
# チェック1: リンク切れ
# ─────────────────────────────────────────────

def check_broken_links(wiki_files: dict) -> list[str]:
    issues = []
    # obsidian_logs の .md もリンク解決対象に含める
    log_stems = {p.stem for p in LOG_DIR.glob("*.md")} if LOG_DIR.exists() else set()
    resolvable = set(wiki_files.keys()) | log_stems

    for stem, path in wiki_files.items():
        if "/" in stem:  # 重複エントリをスキップ
            text  = path.read_text(encoding="utf-8")
            links = collect_links(text)
            for link in links:
                link_clean = link.strip()
                # パス形式・ファイル名形式の両方で解決を試みる
                if link_clean not in resolvable and Path(link_clean).stem not in resolvable:
                    issues.append(f"  {c(RED,'BROKEN')} {stem}.md → [[{link_clean}]]")
    return issues


# ─────────────────────────────────────────────
# チェック2: 孤児ページ
# ─────────────────────────────────────────────

def check_orphan_pages(wiki_files: dict) -> list[str]:
    issues = []
    # どのページから参照されているかを集計（INDEX.mdも含む）
    referenced: set[str] = set()
    for stem, path in wiki_files.items():
        if "/" in stem or stem == "INDEX":
            text  = path.read_text(encoding="utf-8")
            links = collect_links(text)
            for link in links:
                referenced.add(link.strip())
                referenced.add(Path(link.strip()).stem)

    for stem, path in wiki_files.items():
        if "/" not in stem:
            continue
        name = path.stem
        rel  = str(path.relative_to(WIKI_ROOT).with_suffix(""))
        if rel == "INDEX":
            continue  # INDEX.md は孤児チェック除外
        if name not in referenced and rel not in referenced:
            issues.append(f"  {c(YELLOW,'ORPHAN')} {rel}.md はどのページからもリンクされていません")
    return issues


# ─────────────────────────────────────────────
# チェック3: 矛盾検出（同一ティッカーの assessment が食い違う）
# ─────────────────────────────────────────────

def check_contradictions(wiki_files: dict) -> list[str]:
    issues = []
    # tickers/ のフロントマター assessment を収集
    ticker_assessments: dict[str, str] = {}
    for stem, path in wiki_files.items():
        if not stem.startswith("tickers/"):
            continue
        fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        ticker     = fm.get("ticker", path.stem)
        assessment = fm.get("assessment", "")
        if ticker and assessment:
            ticker_assessments[ticker] = assessment

    # tickers/ の last_updated を収集（日付比較用）
    ticker_last_updated: dict[str, datetime] = {}
    for stem, path in wiki_files.items():
        if not stem.startswith("tickers/"):
            continue
        fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        lu = fm.get("last_updated", "")
        try:
            ticker_last_updated[fm.get("ticker", path.stem)] = datetime.strptime(lu, "%Y-%m-%d")
        except ValueError:
            pass

    # concepts/ の観測事例テーブルと照合（BUY vs SELL の食い違いを検出）
    for stem, path in wiki_files.items():
        if not stem.startswith("concepts/"):
            continue
        text = path.read_text(encoding="utf-8")
        # 観測事例テーブルの行を解析: | 日付 | [[tickers/TICKER]] | 成功/失敗 |
        for line in text.splitlines():
            m = re.search(r"\|\s*(\d{4}-\d{2}-\d{2}).*?\[\[tickers/([A-Z]+)\]\].*?\|\s*(成功|失敗)", line)
            if not m:
                continue
            entry_date_str, ticker, concept_outcome = m.group(1), m.group(2), m.group(3)
            page_assessment = ticker_assessments.get(ticker, "")
            # コンセプトエントリが ticker の last_updated より古い場合はスキップ（歴史的記録）
            try:
                entry_date = datetime.strptime(entry_date_str, "%Y-%m-%d")
                last_upd   = ticker_last_updated.get(ticker)
                if last_upd and entry_date < last_upd:
                    continue
            except ValueError:
                pass
            # 概念ページで「成功」なのにティッカーページが SELL → 矛盾の可能性
            if concept_outcome == "成功" and page_assessment == "SELL":
                issues.append(
                    f"  {c(YELLOW,'CONFLICT')} {ticker}: concepts/{stem.split('/')[1]}.md は「成功」"
                    f"だが tickers/{ticker}.md の assessment={page_assessment}"
                )
            elif concept_outcome == "失敗" and page_assessment == "BUY":
                issues.append(
                    f"  {c(YELLOW,'CONFLICT')} {ticker}: concepts/{stem.split('/')[1]}.md は「失敗」"
                    f"だが tickers/{ticker}.md の assessment={page_assessment}"
                )
    return issues


# ─────────────────────────────────────────────
# チェック4: 鮮度チェック
# ─────────────────────────────────────────────

def check_staleness(wiki_files: dict) -> list[str]:
    issues = []
    threshold = datetime.now() - timedelta(days=STALE_DAYS)
    for stem, path in wiki_files.items():
        if not stem.startswith("tickers/"):
            continue
        fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        last_updated = fm.get("last_updated", "")
        if not last_updated:
            continue
        try:
            dt = datetime.strptime(last_updated, "%Y-%m-%d")
            if dt < threshold:
                days_old = (datetime.now() - dt).days
                issues.append(
                    f"  {c(YELLOW,'STALE')} tickers/{path.stem}.md — last_updated={last_updated} ({days_old}日前)"
                )
        except ValueError:
            pass
    return issues


# ─────────────────────────────────────────────
# チェック5: 未リンクの obsidian_logs
# ─────────────────────────────────────────────

def check_unlinked_logs(wiki_files: dict) -> list[str]:
    issues = []
    if not LOG_DIR.exists():
        return issues

    referenced_logs: set[str] = set()
    for stem, path in wiki_files.items():
        if "/" not in stem:
            continue
        text  = path.read_text(encoding="utf-8")
        links = collect_links(text)
        for link in links:
            name = Path(link.strip()).stem
            if name.startswith("Log_"):
                referenced_logs.add(name)

    for log_path in LOG_DIR.glob("Log_*.md"):
        if log_path.stem not in referenced_logs:
            issues.append(
                f"  {c(YELLOW,'UNLINKED')} obsidian_logs/{log_path.name} はWikiから参照されていません"
            )
    return issues


# ─────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────

def main():
    print(f"\n{c(BOLD, '=== Wiki ヘルスチェック ===')} {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    if not WIKI_ROOT.exists():
        print(c(RED, f"[ERROR] wiki/ ディレクトリが存在しません: {WIKI_ROOT}"))
        print("まず `python server_librarian.py --ingest` を実行してください。")
        sys.exit(1)

    wiki_files = collect_wiki_files()
    total_pages = len([s for s in wiki_files if "/" in s])
    print(f"対象ページ数: {total_pages}\n")

    all_issues: dict[str, list[str]] = {}

    all_issues["リンク切れ"] = check_broken_links(wiki_files)
    all_issues["孤児ページ"] = check_orphan_pages(wiki_files)
    all_issues["矛盾検出"]   = check_contradictions(wiki_files)
    all_issues["鮮度"]        = check_staleness(wiki_files)
    all_issues["未リンクLog"] = check_unlinked_logs(wiki_files)

    has_issue = False
    for category, issues in all_issues.items():
        icon = "❌" if issues else "✅"
        count = f"({len(issues)}件)" if issues else ""
        print(f"{icon} {category} {count}")
        for issue in issues:
            print(issue)
            has_issue = True
        if issues:
            print()

    print()
    if has_issue:
        print(c(YELLOW, "警告あり。上記の項目を確認してください。"))
        sys.exit(1)
    else:
        print(c(GREEN, "問題なし。Wikiは健全です。"))
        sys.exit(0)


if __name__ == "__main__":
    main()
