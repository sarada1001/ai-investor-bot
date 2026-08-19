"""engine/trade_cycle.py — メイントレードサイクル (run_trade_cycle)"""

from __future__ import annotations

import datetime

import skills.alpaca_trade            as _alpaca_mod
import skills.training_data_collector as _training_mod
from agents.audit_agent import (
    AuditAgent        as _AuditAgentImpl,
    COACHING_PROMPTS  as _COACHING_PROMPTS,
    load_agent_status as _load_agent_status,
    apply_suspension_to_weights as _apply_suspension_to_weights,
)
from tools.alpaca_client  import AlpacaClient as _AlpacaClient, PORTFOLIO_PATH as _PORTFOLIO_PATH
from tools.circuit_breaker import CircuitBreaker as _CircuitBreaker
from tools.critic_agent    import CriticAgent   as _CriticAgentImpl
from tools.trade_guard     import TradeGuard    as _TradeGuard
from tools.auto_logger     import ObsidianLogger as _ObsidianLogger

from engine.bbs      import BBS
from engine.constants import (
    TARGET_TICKER, STRONG_BUY_SCORE, BUY_QTY,
    WEIGHTS, _W, _STRONG_BUY_LABEL, _HOLD_LABEL,
)
from engine.display  import (
    _log, _sep, _stage_header, _phase_header, _phase_footer,
    _mock_banner, _hybrid_banner, _live_gate_banner,
    _main_header, _decision_box,
)
from engine.notify        import send_line_message
from engine.agent_wrappers import (
    TechnicalAgent, NewsAgent, MacroAgent, SocialAgent, LiquidityAgent,
    FundamentalAgent, ManagerAgent, RiskAgent, ExitAgent,
    _gate_check, _gate_display, _liquidity_trading_score,
)
from engine.mock_helpers  import _run_mock_stage1, _run_mock_stage2, _run_mock_risk
from engine.trade_helpers import (
    _agent_to_weight_key, _compute_effective_weights,
    _fetch_past_lessons, _fetch_wiki_context,
)
from agents.exit_agent import (
    add_position as _portfolio_add,
    update_position_stop_order as _update_position_stop_order,
    reconcile_broker_stops as _reconcile_broker_stops,
)


class _DiscardBBS:
    """Stage 0 をポートフォリオ単位で1回だけ走らせるときの書き込み先。

    ExitAgent は評価結果を BBS に書くが、ウォッチリスト実行では
    結果を各銘柄のセッションファイルへ後から転記する。
    bbs/ に新種のセッションファイルを増やさないためのダミー。
    """

    def write(self, agent_name: str, key: str, data: object) -> None:
        return None


def init_alpaca_and_sync(mock_mode: bool = False):
    """Alpaca クライアントを初期化し portfolio.json を同期する。

    初期化に失敗した場合は None を返す（呼び出し側は dry_run 相当で継続）。
    run_trade_cycle と run_watchlist_cycle の Stage 0 から共用する。
    """
    if mock_mode:
        return None
    try:
        alpaca = _AlpacaClient()
        _log("[Portfolio] Alpaca ポジションと portfolio.json を同期中...")
        _sync = alpaca.sync_portfolio(_PORTFOLIO_PATH)
        _log(f"[Portfolio] 同期完了: Alpaca={_sync['alpaca_positions']} 件  "
             f"追加={_sync['added']}  除去={_sync['removed']}")

        # broker stop の整合性チェック（起動時 reconcile）
        _recon = _reconcile_broker_stops(alpaca)
        if _recon.get("missing"):
            _log(f"[BrokerStop] ⚠️  CRITICAL: broker stop 消失 — {_recon['missing']}")
        if _recon.get("no_stop"):
            _log(f"[BrokerStop] ⚠️  WARNING: broker stop なし — {_recon['no_stop']}")
        if _recon.get("refilled"):
            _log(f"[BrokerStop] ℹ broker stop 約定済み — {_recon['refilled']}")

        is_open, market_msg = alpaca.is_market_open()
        market_icon = "🟢" if is_open else "🔴"
        _log(f"[Market] {market_icon} {market_msg}")
        return alpaca
    except Exception as _e:
        _log(f"[Alpaca] 初期化エラー: {_e} — dry_run モードで継続")
        return None


def run_exit_stage(
    bbs                = None,
    mock_mode:   bool  = False,
    alpaca_client      = None,
    notify_line: bool  = False,
) -> list[dict]:
    """Stage 0（Selling Loop）を1回実行し、売却判断リストを返す。

    保有ポジションの評価はポートフォリオ単位であり、分析対象銘柄には依存しない。
    そのためウォッチリスト実行では run_watchlist_cycle() が銘柄ループの前に
    ここを1度だけ呼び、結果を各 run_trade_cycle() へ渡す
    （銘柄数だけ ExitAgent の thesis LLM 判定が重複するのを防ぐため）。

    bbs=None のときは書き込みを捨てる。呼び出し側が結果を受け取り、
    銘柄ごとの BBS へ転記する前提。
    """
    _stage_header(0, "Selling Loop  [ExitAgent]")
    exit_results = ExitAgent(bbs if bbs is not None else _DiscardBBS()).run(
        mock_mode=mock_mode,
        alpaca_client=alpaca_client,
        phase_tag="S0",
    )
    if exit_results:
        sell_tickers = [r["ticker"] for r in exit_results if r["action"] == "SELL"]
        if sell_tickers and notify_line:
            for r in [r for r in exit_results if r["action"] == "SELL"]:
                order = r.get("order_result", {})
                send_line_message(
                    f"【ECC 売却実行】{r['ticker']}\n"
                    f"種別: {r['exit_type']}\n"
                    f"理由: {r['reason']}\n"
                    f"損益: {r['pnl_pct']:+.2f}%\n"
                    + (f"注文ID: {order.get('order_id')}" if order.get("order_id") else "")
                )
    return exit_results


def run_trade_cycle(
    ticker:           str             = TARGET_TICKER,
    dry_run:          bool            = False,
    notify_line:      bool            = False,
    mock_mode:        bool            = False,
    hybrid_mode:      bool            = False,
    excluded_agents:  list[str] | None = None,
    run_audit:        bool            = False,
    research_mode:    bool            = False,
    exit_results:     list[dict] | None = None,
) -> dict:
    """
    スイングトレード分析サイクルをステージゲート方式で実行する。

    Stage 1: TechnicalAgent + NewsAgent + MacroAgent（安価スキャン）
    Gate   : マクロ NEGATIVE → ブレーキ HOLD / Tech・News 双方 NEUTRAL → HOLD
    Stage 2: FundamentalAgent（Gate 通過時のみ）
    Stage 3: ManagerAgent（最終評価 & 発注）

    research_mode=True の場合:
        - Alpaca 発注を完全無効化（dry_run=True を強制）
        - LINE 通知を無効化
        - Obsidian / TradeGuard への書き込みを無効化
        - HOLD 判断を data/research/hold_cases.jsonl に記録

    exit_results:
        None（既定）なら Stage 0 をこのサイクル内で実行する（単一銘柄モード）。
        リストが渡された場合は上流（run_watchlist_cycle）で実行済みとみなし、
        判定を再実行せず結果を BBS に転記するだけにする。
    """
    # 研究モード: 発注・通知・外部書き込みを全て無効化
    if research_mode:
        dry_run     = True
        notify_line = False

    excluded_agents = excluded_agents or []
    excluded_keys: list[str] = list({
        k for a in excluded_agents
        if (k := _agent_to_weight_key(a)) is not None
    })

    # ── AuditAgent: 評価 & SUSPENDED エージェントのウェイト調整 ──
    _audit = _AuditAgentImpl()
    if run_audit:
        _log("[AuditAgent] エージェント成績を評価中 (agent_status.json 更新)...")
        _audit.run_evaluation()

    _agent_status = _load_agent_status()
    _suspended_w, _suspended_keys = _apply_suspension_to_weights(
        _compute_effective_weights(excluded_keys), _agent_status
    )
    eff_weights = _suspended_w

    session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    _main_header(ticker, session_id)

    _suspended_agents = [
        name for name, info in _agent_status.items()
        if info.get("status") == "SUSPENDED"
    ]
    if _suspended_agents:
        _log(f"[AuditAgent] 🔴 SUSPENDED エージェント: {_suspended_agents}")
        _log(f"[AuditAgent] Shadow Mode で処理 → 本番スコアへの影響なし")
        _log(f"[AuditAgent] 有効ウェイト: { {k: f'{v:.3f}' for k, v in eff_weights.items()} }")

    if excluded_keys:
        _log(f"[アブレーション] 除外エージェント: {excluded_agents}  "
             f"→ 有効ウェイト: {eff_weights}")
    if mock_mode:
        _mock_banner()
    elif hybrid_mode:
        _hybrid_banner()
    else:
        _live_gate_banner(dry_run=dry_run)

    bbs = BBS(session_id)

    def _write_excluded(agent_name: str, bbs_key: str) -> None:
        bbs.write(agent_name, bbs_key, {"trend": "neutral", "excluded": True,
                                        "trend_reason": f"{agent_name} 除外済"})

    # ── Alpaca クライアント初期化 & portfolio 同期 ──────────────────
    _alpaca: _AlpacaClient | None = init_alpaca_and_sync(mock_mode)

    # ── Stage 0: Selling Loop（保有ポジション売却チェック）─────────
    # 保有監視はポートフォリオ単位なので、ウォッチリスト実行では
    # run_watchlist_cycle() が銘柄ループ前に1回だけ実行し、その結果を
    # exit_results で受け取る。ここでは判定を再実行せず BBS に転記する。
    if exit_results is None:
        run_exit_stage(
            bbs,
            mock_mode     = mock_mode,
            alpaca_client = _alpaca if not dry_run else None,
            notify_line   = notify_line,
        )
    else:
        _log("[ExitAgent] Stage 0 はサイクル冒頭で実行済み — 判定結果を BBS に転記します")
        bbs.write("ExitAgent", "exit_decisions", {
            "date":    datetime.date.today().isoformat(),
            "results": exit_results,
        })

    _account_equity: float = 0.0

    # ── Circuit Breaker チェック ──────────────────────────────────
    if not mock_mode and _alpaca is not None:
        try:
            _acc_info  = _alpaca.get_account()
            _equity    = _acc_info.get("equity", 0.0)
            _cb        = _CircuitBreaker()
            _cb_result = _cb.check(_equity)

            if not _cb_result.buy_allowed:
                _icon = "🚨" if _cb_result.status == "HARD_TRIP" else "⚡"
                _log(f"[CircuitBreaker] {_icon} {_cb_result.status} 発動 — 新規 BUY を停止")
                _log(f"  日次損益   : {_cb_result.daily_pnl_pct:+.2f}%")
                _log(f"  高値比DD   : {_cb_result.total_drawdown_pct:+.2f}%")
                _log(f"  理由       : {_cb_result.reason}")

                _cb_judgment = {
                    "ticker":        ticker,
                    "decision":      _HOLD_LABEL,
                    "score":         0.0,
                    "threshold":     STRONG_BUY_SCORE,
                    "signals":       {"news": 0.0, "technical": 0.0, "macro": 0.0,
                                      "fundamental": 0.0, "social": 0.0},
                    "gate_skipped":  True,
                    "gate_reason":   f"CircuitBreaker {_cb_result.status}: {_cb_result.reason}",
                    "circuit_trip":  _cb_result.status,
                    "rationale":     f"CircuitBreaker {_cb_result.status}により新規 BUY を停止",
                    "order":         None,
                    "dry_run":       dry_run,
                }
                bbs.write("CircuitBreaker", "manager_judgment", _cb_judgment)
                _decision_box([
                    f"{'─' * (_W - 2)}",
                    f"  CircuitBreaker: {_cb_result.status}",
                    f"{'─' * (_W - 2)}",
                    f"  銘柄         : {ticker}",
                    "  判断         : ⏸  HOLD（新規 BUY 停止）",
                    f"  日次損益     : {_cb_result.daily_pnl_pct:+.2f}%",
                    f"  高値比 DD    : {_cb_result.total_drawdown_pct:+.2f}%",
                    f"  理由         : {_cb_result.reason[:55]}",
                    f"{'─' * (_W - 2)}",
                    f"  BBS ログ     : {bbs.path}",
                ])
                record_id = _training_mod.save_training_record(
                    session_id=session_id, ticker=ticker,
                    bbs_entries=bbs.read_all(), judgment=_cb_judgment,
                    mock_mode=mock_mode, hybrid_mode=hybrid_mode,
                )
                _log(f"[学習データ] 保存完了: record_id={record_id}")
                return _cb_judgment
            else:
                _log(
                    f"[CircuitBreaker] 🟢 OPEN  "
                    f"日次{_cb_result.daily_pnl_pct:+.2f}%  "
                    f"高値比{_cb_result.total_drawdown_pct:+.2f}%"
                )
                _account_equity = _equity
        except Exception as _cb_err:
            _log(f"[CircuitBreaker] チェックエラー（スキップ）: {_cb_err}")

    # ── Stage 1: 安価シグナルスキャン ────────────────────────────
    _stage_header(1, "安価シグナルスキャン  [Technical + News + Macro + Social + Liquidity]")

    def _run_shadow_agent(
        agent_name: str,
        bbs_key: str,
        run_fn,
    ) -> None:
        _log(f"  🔴 [SUSPENDED → Shadow Mode] {agent_name} を仮想判定で実行中...")
        run_fn()
        shadow_result = bbs.read(bbs_key) or {}
        coaching = _COACHING_PROMPTS.get(agent_name, "")
        bbs.write("AuditAgent", f"shadow_{bbs_key}", {
            **shadow_result, "_shadow_mode": True, "_coaching_prompt": coaching,
        })
        bbs.write(agent_name, bbs_key, {
            "trend": "neutral", "suspended": True,
            "trend_reason": f"{agent_name} SUSPENDED (shadow mode) — ウェイト=0",
        })
        _log(f"  🔴 Shadow 結果を shadow_{bbs_key} に保存。本番 BBS は NEUTRAL で上書き。")

    if mock_mode:
        _run_mock_stage1(
            bbs, ticker,
            excluded_keys=excluded_keys,
            suspended_keys=_suspended_keys,
        )
    else:
        if "technical" in excluded_keys:
            _write_excluded("TechnicalAgent", "technical_analysis")
        elif "technical" in _suspended_keys:
            _run_shadow_agent(
                "TechnicalAgent", "technical_analysis",
                lambda: TechnicalAgent(bbs).run(ticker, phase_tag="S1-1/5-SHADOW"),
            )
        else:
            TechnicalAgent(bbs).run(ticker, phase_tag="S1-1/5")

        if "news" in excluded_keys:
            _write_excluded("NewsAgent", "news_analysis")
        elif "news" in _suspended_keys:
            _run_shadow_agent(
                "NewsAgent", "news_analysis",
                lambda: NewsAgent(bbs).run(ticker, phase_tag="S1-2/5-SHADOW"),
            )
        else:
            NewsAgent(bbs).run(ticker, phase_tag="S1-2/5")

        if "macro" in excluded_keys:
            _write_excluded("MacroAgent", "macro_analysis")
        elif "macro" in _suspended_keys:
            _run_shadow_agent(
                "MacroAgent", "macro_analysis",
                lambda: MacroAgent(bbs).run(phase_tag="S1-3/5-SHADOW"),
            )
        else:
            MacroAgent(bbs).run(phase_tag="S1-3/5")

        if "social" in excluded_keys:
            _write_excluded("SocialAgent", "social_analysis")
        elif "social" in _suspended_keys:
            _run_shadow_agent(
                "SocialAgent", "social_analysis",
                lambda: SocialAgent(bbs).run(ticker, phase_tag="S1-4/5-SHADOW"),
            )
        else:
            SocialAgent(bbs).run(ticker, phase_tag="S1-4/5")

        if "liquidity" in excluded_keys:
            bbs.write("LiquidityAgent", "liquidity_analysis",
                      {"verbal_annotation": "LiquidityAgent 除外済", "score": 0.0,
                       "ask_ratio": 0.5, "bid_ratio": 0.5, "net_large_inflow": 0.0,
                       "net_small_inflow": 0.0, "pressure": "neutral",
                       "excluded": True, "trend": "neutral",
                       "trend_reason": "LiquidityAgent 除外済"})
        elif "liquidity" in _suspended_keys:
            _run_shadow_agent(
                "LiquidityAgent", "liquidity_analysis",
                lambda: LiquidityAgent(bbs).run(ticker, phase_tag="S1-5/5-SHADOW"),
            )
        else:
            LiquidityAgent(bbs).run(ticker, phase_tag="S1-5/5")

    # ── Gate: マクロブレーキ / 双方 NEUTRAL → HOLD 即終了 ────────
    gate = _gate_check(bbs, ticker)
    _gate_display(gate)

    if gate["skip_fundamental"]:
        _social_gate     = bbs.read("social_analysis")    or {}
        _liq_gate        = bbs.read("liquidity_analysis") or {}
        _sg_sentiment    = _social_gate.get("sentiment", "NEUTRAL").upper()
        _sg_hype         = float(_social_gate.get("hype_score", 0.0))
        _sg_sig_gate_raw = {"POSITIVE": +1.0, "NEUTRAL": 0.0, "NEGATIVE": -1.0}.get(_sg_sentiment, 0.0)
        _sg_sig_gate     = _sg_sig_gate_raw
        # mock / mock_fallback 時は 0.0（実データのみ寄与可）
        _liq_sig_gate    = _liquidity_trading_score(_liq_gate)
        score = round(
            gate["news_signal"]    * eff_weights["news"]
            + gate["tech_signal"]  * eff_weights["technical"]
            + gate["macro_signal"] * eff_weights["macro"]
            + _sg_sig_gate         * eff_weights["social"]
            + _liq_sig_gate        * eff_weights.get("liquidity", 0.0),
            4,
        )
        brake_label = "マクロブレーキ" if gate["macro_brake"] else "シグナル不足"
        judgment = {
            "ticker":    ticker,
            "decision":  _HOLD_LABEL,
            "score":     score,
            "threshold": STRONG_BUY_SCORE,
            "signals": {
                "news":        gate["news_signal"],
                "technical":   gate["tech_signal"],
                "macro":       gate["macro_signal"],
                "fundamental": 0.0,
                "social":      _sg_sig_gate,
                "liquidity":   _liq_sig_gate,
            },
            "gate_skipped":  True,
            "gate_reason":   gate["reason"],
            "macro_brake":   gate["macro_brake"],
            "rationale":     f"Gate: {gate['reason']}",
            "order":  None,
            "dry_run": dry_run,
        }
        # HOLD 原因エージェントの特定（research_mode の有無に関わらず付与）
        from engine.research_helpers import _identify_hold_causes
        _gate_causes = _identify_hold_causes(
            sigs=judgment["signals"],
            weights=eff_weights,
            macro_forced_hold=gate["macro_brake"],
        )
        judgment["primary_reason_agent"] = _gate_causes["primary"]
        judgment["contributing_agents"]  = _gate_causes["contributing"]

        # 研究モード: hold_cases.jsonl に Gate HOLD を記録
        if research_mode:
            from engine.research_helpers import _save_hold_case
            _save_hold_case(ticker, judgment, bbs, "gate", eff_weights)

        bbs.write("GateAgent", "manager_judgment", judgment)

        _decision_box([
            f"{'─' * (_W - 2)}",
            f"  Gate 判断: HOLD（{brake_label}）",
            f"{'─' * (_W - 2)}",
            f"  銘柄         : {ticker}",
            "  判断         : ⏸  HOLD",
            f"  理由         : {gate['reason'][:55]}",
            "  スキップ     : FundamentalAgent (トークンコスト節約)",
            f"{'─' * (_W - 2)}",
            f"  BBS ログ     : {bbs.path}",
        ])

        if mock_mode:
            _mock_banner("テスト実行完了（Gate: HOLD）。実際のAPIは一切呼び出されていません。")
        elif hybrid_mode:
            _hybrid_banner("ハイブリッド実行完了（Gate: HOLD）。市場データはリアル、発注はスキップされました。")
        record_id = _training_mod.save_training_record(
            session_id=session_id, ticker=ticker,
            bbs_entries=bbs.read_all(), judgment=judgment,
            mock_mode=mock_mode, hybrid_mode=hybrid_mode,
        )
        _log(f"[学習データ] 保存完了: record_id={record_id}")
        return judgment

    # ── Stage 2: Fundamental 深層分析 ────────────────────────────
    _stage_header(2, "ファンダメンタルズ深層分析  [FundamentalAgent]")
    if "fundamental" in excluded_keys:
        _log("  ⚠️  [アブレーション] FundamentalAgent を除外 → NEUTRAL エントリを書き込み")
        bbs.write("FundamentalAgent", "fundamental_analysis",
                  {"trend": "neutral", "excluded": True, "trend_reason": "FundamentalAgent 除外済"})
    elif "fundamental" in _suspended_keys:
        _log("  🔴 [SUSPENDED] FundamentalAgent — Shadow Mode (本番スコア影響なし)")
        if mock_mode:
            _run_mock_stage2(bbs, ticker)
            _shadow_fa = bbs.read("fundamental_analysis") or {}
        else:
            FundamentalAgent(bbs).run(ticker, phase_tag="S2-SHADOW", allow_edgar_fetch=False)
            _shadow_fa = bbs.read("fundamental_analysis") or {}
        bbs.write("AuditAgent", "shadow_fundamental_analysis", {
            **_shadow_fa, "_shadow_mode": True,
            "_coaching_prompt": _COACHING_PROMPTS.get("FundamentalAgent", ""),
        })
        bbs.write("FundamentalAgent", "fundamental_analysis", {
            "trend": "neutral", "suspended": True,
            "trend_reason": "FundamentalAgent SUSPENDED (shadow mode) — ウェイト=0",
        })
    elif mock_mode:
        _run_mock_stage2(bbs, ticker)
    else:
        FundamentalAgent(bbs).run(
            ticker,
            phase_tag="S2",
            allow_edgar_fetch=True,
        )

    # ── Stage 3: 最終評価 & 発注判断 ─────────────────────────────
    _stage_header(3, "最終評価 & 発注判断  [ManagerAgent]")
    judgment = ManagerAgent(bbs).run(
        ticker, dry_run=dry_run, phase_tag="S3", mock_mode=mock_mode,
        effective_weights=eff_weights, excluded_keys=excluded_keys,
        research_mode=research_mode,
    )

    # 研究モード: Manager HOLD を hold_cases.jsonl に記録
    if research_mode and not judgment.get("is_strong_buy"):
        from engine.research_helpers import _save_hold_case
        _save_hold_case(ticker, judgment, bbs, "manager", eff_weights)

    # ── Stage 4: リスク管理 & ポジションサイジング（STRONG BUY 時のみ）──
    order_result: dict | None = None
    if judgment.get("is_strong_buy"):
        _stage_header(4, "リスク管理 & ポジションサイジング  [RiskAgent]")
        if mock_mode:
            _run_mock_risk(bbs, ticker)
        else:
            RiskAgent(bbs).run(ticker, account_equity=_account_equity or None)

        risk_data  = bbs.read("risk_analysis") or {}
        rec_shares = risk_data.get("recommended_shares", 1)

        # ── CriticAgent による監査 ────────────────────────────────
        proceed_with_buy = True
        if not mock_mode and not dry_run and not hybrid_mode:
            _phase_header("S4.5", "CriticAgent")
            _log("ManagerAgent の判断を過去教訓で監査中...")
            _sep()
            _critic_rules = _fetch_past_lessons(ticker)
            _critic       = _CriticAgentImpl()
            _critic_res   = _critic.evaluate_trade(
                ticker          = ticker,
                manager_action  = judgment.get("decision", "HOLD"),
                manager_context = judgment.get("rationale", ""),
                retrieved_rules = _critic_rules,
            )
            _is_fallback = "API障害" in _critic_res.get("critique_reason", "") or \
                           "フォールバック" in _critic_res.get("critique_reason", "")
            _cd   = _critic_res.get("critic_decision", "HOLD")
            _icon_c = "✅" if _cd == "APPROVE" else ("⚠️" if _is_fallback else "❌")
            _log(f"{_icon_c} 判定: {_cd}  理由: {_critic_res.get('critique_reason','')[:65]}")

            if _cd == "OVERRIDE":
                _log("❌ CriticAgent OVERRIDE → 発注キャンセル")
                proceed_with_buy = False
                judgment["critic_override"] = True
                judgment["critic_reason"]   = _critic_res.get("critique_reason")
            elif _is_fallback:
                _log("⚠️  Ollama 未接続（フォールバック） → CriticAgent スキップ、発注継続")
                judgment["critic_override"] = False
                judgment["critic_reason"]   = _critic_res.get("critique_reason")
            else:
                _log(f"✅ {_cd} → BUY 継続")
            bbs.write("CriticAgent", "critic_judgment", _critic_res)
            _phase_footer()

        # ── TradeGuard チェック ──────────────────────────────────
        if proceed_with_buy and not dry_run and not mock_mode and not hybrid_mode:
            _tg = _TradeGuard()
            _open_pos    = len(_alpaca.get_positions()) if _alpaca is not None else 0
            _order_value = rec_shares * risk_data.get("current_price", 0.0)
            _gr = _tg.check_pre_buy(
                ticker=ticker, order_value=_order_value,
                account_equity=_account_equity, open_positions=_open_pos,
            )
            if not _gr.allowed:
                _log(f"  🛡 [TradeGuard] 発注ブロック: {_gr.reason}")
                proceed_with_buy = False
                judgment["guard_blocked"] = True
                judgment["guard_reason"]  = _gr.reason

        # ── 発注 ─────────────────────────────────────────────────
        _sep()
        _log(f"Alpaca に {ticker} {rec_shares}株 買い注文を送信します...")
        if not proceed_with_buy:
            skip_reason = judgment.get("guard_reason") or "CriticAgent OVERRIDE"
            _log(f"  発注スキップ: {skip_reason}")
            order_result = {"skipped": True, "skip_reason": skip_reason}
        elif dry_run or mock_mode or hybrid_mode:
            label = "hybrid_mode" if hybrid_mode else ("mock_mode" if mock_mode else "dry_run")
            _log(f"  ({label}=True のため実際の発注はスキップ)")
            order_result = {
                "dry_run": True, "mock": mock_mode or hybrid_mode,
                "symbol": ticker, "qty": rec_shares, "side": "buy",
            }
        elif _alpaca is not None:
            order_result = _alpaca.place_buy(ticker, rec_shares)
            if order_result.get("success"):
                _log(f"  ✅ 注文受付完了: order_id={order_result.get('order_id')}")
                _log(f"     status  : {order_result.get('status')}")
                _log(f"     symbol  : {order_result.get('symbol')} × {order_result.get('qty')} 株")
                # Paper/Live API は注文直後に PENDING_NEW / filled_qty=0 を返し、
                # 数秒後に FILLED に遷移する。broker stop は約定済み数量で作るため、
                # ここで最大 20 秒ポーリングして最新 filled_qty / fill_price を取得する。
                _oid = order_result.get("order_id")
                if _oid:
                    _log(f"  [BuyFill] 約定確認ポーリング中 (max 20s)...")
                    _fill_poll = _alpaca.wait_for_fill(_oid)
                    if _fill_poll.get("success") and _fill_poll.get("terminal"):
                        order_result["filled_qty"] = _fill_poll.get("filled_qty", order_result.get("filled_qty", 0))
                        _fp = _fill_poll.get("fill_price")
                        if _fp:
                            order_result["fill_price"] = _fp
                        _log(
                            f"  [BuyFill] ✅ 約定確認: status={_fill_poll.get('status')}"
                            f"  filled_qty={order_result['filled_qty']:.0f}"
                        )
                    elif _fill_poll.get("timed_out"):
                        _log("  [BuyFill] ⚠️  タイムアウト — 次回 reconcile で再確認します")
                    else:
                        _log(f"  [BuyFill] ⚠️  API エラー — 次回 reconcile で再確認します")
                if notify_line:
                    _buy_price = order_result.get("fill_price") or risk_data.get("current_price", 0.0)
                    send_line_message(
                        f"【ECC {ticker} BUY約定】\n"
                        f"{rec_shares}株 @ ${_buy_price:.2f}"
                    )
            elif order_result.get("skipped"):
                _log(f"  ⏭  注文スキップ: {order_result.get('skip_reason')}")
            else:
                _log(f"  ❌ 発注エラー: {order_result.get('error')}")
        else:
            order_result = {"error": "AlpacaClient 未初期化 — 発注不可"}
            _log(f"  ❌ AlpacaClient 未初期化: 設定を確認してください")

        judgment["order"] = order_result
        judgment["current_price"] = risk_data.get("current_price")
        bbs.write("ManagerAgent", "manager_judgment", judgment)

        # ── ポートフォリオ登録 ────────────────────────────────────
        # 「注文受付成功」≠「約定成功」。実 BUY 注文が Alpaca に受け付けられたときのみ
        # portfolio.json / TradeGuard へ書き込む。
        # dry_run / mock / hybrid は order_result["dry_run"]=True を返すが、
        # これは架空ポジションであり実 portfolio へ登録してはならない。
        _order_ok = (
            not research_mode
            and not dry_run
            and not mock_mode
            and not hybrid_mode
            and bool(order_result.get("success"))
        )
        if _order_ok:
            try:
                _entry_price  = risk_data.get("current_price", 0.0)
                _stop_price   = risk_data.get("stop_loss_price")
                _fill_price   = order_result.get("fill_price")
                _actual_entry = _fill_price or _entry_price
                if not _actual_entry or _actual_entry <= 0:
                    _log(f"  [Portfolio] ❌ entry_price が無効 ({_actual_entry}) — portfolio.json への登録をスキップします")
                else:
                    _buy_log = _ObsidianLogger().save_log({
                        "ticker":  ticker,
                        "action":  "BUY",
                        "context": (
                            f"{judgment.get('rationale', '(根拠なし)')}\n"
                            + (
                                f"--- Alpaca 注文 ---\n"
                                f"注文ID: {order_result.get('order_id', 'N/A')}\n"
                                f"ステータス: {order_result.get('status', 'N/A')}\n"
                                f"約定価格: ${_fill_price:.2f}" if _fill_price else ""
                            )
                        ),
                        "tags": ["entry", ticker.lower(), session_id],
                    })
                    _log(f"  [Obsidian] 購入ログ保存: {_buy_log.name}")
                    _atr_tp = risk_data.get("take_profit_price")
                    _target = _atr_tp if _atr_tp else (
                        round(_actual_entry * 1.10, 2) if _actual_entry else None
                    )
                    _entry_order_id = order_result.get("order_id")
                    _portfolio_add(
                        ticker              = ticker,
                        entry_price         = _actual_entry,
                        shares              = rec_shares,
                        target_price        = _target,
                        stop_loss_price     = _stop_price,
                        buy_log_file        = _buy_log.name,
                        thesis              = judgment.get("rationale", ""),
                        entry_order_id      = _entry_order_id,
                        broker_stop_status  = "none",
                    )
                    _log(f"  [Portfolio] {ticker} ×{rec_shares} を portfolio.json に登録しました")
                    _TradeGuard().record_buy(ticker)

                    # ── broker-side stop 作成（live / paper 共通、dry_run/mock 除く）──
                    # BUY が約定済みの場合のみ stop を作成する。
                    # filled_qty=0 はまだ未約定（指値が刺さっていない）を意味するので
                    # stop を作らず CRITICAL ログで無保護状態を明示する。
                    # 部分約定は filled_qty 分だけ stop を作成し、残りは未保護として警告する。
                    _is_real_order = (
                        not dry_run and not mock_mode and not hybrid_mode
                        and order_result.get("success")
                        and _alpaca is not None
                        and _stop_price
                    )
                    if _is_real_order:
                        _filled_qty   = float(order_result.get("filled_qty") or 0)
                        _req_qty      = float(order_result.get("requested_qty") or rec_shares)
                        if _filled_qty > 0:
                            _stop_res = _alpaca.place_broker_stop(ticker, _filled_qty, _stop_price)
                            if _stop_res.get("success"):
                                _log(
                                    f"  [BrokerStop] ✅ stop 作成: "
                                    f"id={_stop_res['order_id']}  "
                                    f"stop=${_stop_price:.2f}  qty={_filled_qty:.0f}"
                                )
                                _update_position_stop_order(
                                    ticker, _stop_res["order_id"], "open",
                                    entry_order_id=_entry_order_id,
                                )
                                if _filled_qty < _req_qty:
                                    _log(
                                        f"  [BrokerStop] ⚠️  部分約定: "
                                        f"{_filled_qty:.0f}/{_req_qty:.0f} 株のみ保護"
                                        f" — 残り {_req_qty - _filled_qty:.0f} 株は無保護"
                                    )
                            else:
                                import logging as _log_mod
                                _log_mod.getLogger(__name__).critical(
                                    "[BrokerStop] CRITICAL: %s BUY 約定済みだが broker stop 作成失敗 "
                                    "— ポジションが無保護状態 (%s)",
                                    ticker, _stop_res.get("error"),
                                )
                                _log(
                                    f"  [BrokerStop] ❌ CRITICAL: stop 作成失敗 — "
                                    f"無保護状態です: {_stop_res.get('error')}"
                                )
                                _update_position_stop_order(ticker, None, "error")
                        else:
                            import logging as _log_mod
                            _log_mod.getLogger(__name__).warning(
                                "[BrokerStop] %s BUY 注文が未約定 (filled_qty=0) — "
                                "broker stop を作成できません。約定後に手動確認が必要",
                                ticker,
                            )
                            _log(
                                f"  [BrokerStop] ⚠️  BUY 未約定 (filled_qty=0) — "
                                f"broker stop 作成を保留中"
                            )
                            _update_position_stop_order(ticker, None, "pending_fill")
            except Exception as e:
                _log(f"  [Portfolio] 登録エラー（ログは保存済）: {e}")

        elif order_result and order_result.get("skipped") and judgment.get("critic_override"):
            try:
                _cr_reason = (
                    judgment.get("critic_reason")
                    or order_result.get("skip_reason", "CriticAgent OVERRIDE")
                )
                _price = risk_data.get("current_price", "N/A")
                _atr   = risk_data.get("atr", "N/A")
                _sl    = risk_data.get("stop_loss_price", "N/A")
                _tp    = risk_data.get("take_profit_price", "N/A")
                _skip_log = _ObsidianLogger().save_log({
                    "ticker":  ticker,
                    "action":  "SKIPPED",
                    "outcome": "OVERRIDE",
                    "context": (
                        f"スコア: {judgment.get('score', 0):+.4f}  "
                        f"(閾値: {STRONG_BUY_SCORE})\n"
                        f"{judgment.get('rationale', '(根拠なし)')}"
                    ),
                    "root_cause": _cr_reason,
                    "risk_summary": (
                        f"現在価格: ${_price}  / ATR(14): ${_atr}\n"
                        f"ストップロス: ${_sl}  / 利益確定: ${_tp}\n"
                        f"推奨株数: {rec_shares}株"
                    ),
                    "rule_for_future": (
                        "CriticAgentの拒否判断と実際のその後の株価推移を照合し、"
                        "過去教訓の適切性を定期的に検証すること。"
                    ),
                    "profit_loss": "N/A",
                    "tags": ["skipped", "critic_override", ticker.lower(), session_id],
                })
                _log(f"  [Obsidian] SKIPPED ログ保存: {_skip_log.name}")
            except Exception as e:
                _log(f"  [Obsidian] SKIPPED ログ保存エラー: {e}")

    # ── 最終結果表示 ─────────────────────────────────────────────
    decision   = judgment.get("decision", _HOLD_LABEL)
    score      = judgment.get("score", 0.0)
    order      = judgment.get("order") or {}
    risk_data  = bbs.read("risk_analysis") or {}
    rec_shares = risk_data.get("recommended_shares", BUY_QTY)
    stop_price = risk_data.get("stop_loss_price")
    stop_pct   = risk_data.get("stop_loss_pct", 0)
    icon       = "🚀" if decision == _STRONG_BUY_LABEL else "⏸"

    order_line = (
        f"  Alpaca 注文  : {ticker} × {rec_shares} 株  "
        f"[{order.get('status', order.get('error', '-'))}]"
        if order else
        "  Alpaca 注文  : なし（見送り）"
    )
    box_lines = [
        f"{'─' * (_W - 2)}",
        "  ManagerAgent 最終決断",
        f"{'─' * (_W - 2)}",
        f"  銘柄         : {ticker}",
        f"  判断         : {icon}  {decision}",
        f"  加重スコア   : {score:+.4f}  (Strong Buy 閾値: {STRONG_BUY_SCORE:.2f})",
        f"  根拠         : {judgment.get('rationale', '')[:60]}",
        f"{'─' * (_W - 2)}",
        order_line,
    ]
    if stop_price:
        box_lines.append(f"  ストップロス : ${stop_price:.2f}  (-{stop_pct:.2f}%)")
    box_lines += [
        f"{'─' * (_W - 2)}",
        f"  BBS ログ     : {bbs.path}",
    ]
    _decision_box(box_lines)

    if mock_mode:
        _mock_banner("テスト実行完了。実際のAPIは一切呼び出されていません。")
    elif hybrid_mode:
        _hybrid_banner("ハイブリッド実行完了。市場データはリアル、発注はスキップされました。")
    record_id = _training_mod.save_training_record(
        session_id=session_id,
        ticker=ticker,
        bbs_entries=bbs.read_all(),
        judgment=judgment,
        mock_mode=mock_mode,
        hybrid_mode=hybrid_mode,
    )
    _log(f"[学習データ] 保存完了: record_id={record_id}")
    return judgment
