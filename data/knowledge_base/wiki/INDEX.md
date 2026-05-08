# 投資ナレッジベース INDEX

> 最終更新: 2026-05-08 08:17
> 管理スクリプト: `python server_librarian.py --ingest`

## 現在の投資方針

AIエージェントの直近判断をもとに自動生成。詳細は各ティッカーページを参照。

## 保有ポジション

| 銘柄 | 購入日 | 購入単価 | 株数 | ステータス |
|---|---|---|---|---|
| [[tickers/HSY]] |  | $0.00 | 134 | OPEN |

## ティッカー別ページ

- [[tickers/AAPL]] — Apple Inc. | BUY | 2026-05-08
- [[tickers/GEHC]] — GE HealthCare Technologies Inc. | BUY | 2026-05-07
- [[tickers/HSY]] — The Hershey Company | BUY | 2026-05-08
- [[tickers/NKE]] — NIKE, Inc. | HOLD | 2026-05-07
- [[tickers/NVDA]] — NVIDIA Corporation | BUY | 2026-05-07

## コンセプトページ

- [[concepts/breakout]] — ブレイクアウト
- [[concepts/composite_signal]] — 複合シグナル
- [[concepts/confirmation_bias_reduction]] — 確証バイアス軽減
- [[concepts/earnings_beat]] — 好決算・決算プレイ
- [[concepts/emotional_control]] — 感情コントロール
- [[concepts/entry_strategy]] — エントリー戦略・条件
- [[concepts/fundamental_analysis]] — ファンダメンタルズ分析
- [[concepts/fundamental_technical_analysis]] — ファンダメンタルズ・テクニカル分析
- [[concepts/infra_failure_judgment_drift]] — インフラ障害による判断乖離リスク
- [[concepts/llm_based_analysis]] — LLMに基づく分析
- [[concepts/low_vix]] — 低ボラティリティ環境
- [[concepts/macd]] — MACD (移動平均収束拡散法)
- [[concepts/market_sentiment]] — 市場センチメント
- [[concepts/market_sentiment_analysis]] — 市場センチメント分析
- [[concepts/news_based_trading]] — ニュースに基づく取引
- [[concepts/predefined_target_price]] — 目標株価設定
- [[concepts/price_target]] — 目標株価設定
- [[concepts/risk_management]] — リスク管理
- [[concepts/risk_on_market]] — リスクオン市場
- [[concepts/sma_divergence]] — SMA乖離
- [[concepts/sns_sentiment]] — SNSセンチメント
- [[concepts/social_hype]] — SNS煽り
- [[concepts/social_hype_factor]] — SNS煽り要因
- [[concepts/social_media_hype]] — SNS煽り
- [[concepts/social_sentiment]] — SNSセンチメント
- [[concepts/stop_loss]] — ストップロス
- [[concepts/take_profit]] — 利確戦略
- [[concepts/take_profit_strategy]] — 利確戦略
- [[concepts/technical_analysis]] — テクニカル分析
- [[concepts/thesis_driven_trading]] — テーゼドリブン取引
- [[concepts/vix_filtering]] — VIXフィルタリング

## 最近の Ingest 履歴

```
2026-05-06 13:56 | INGEST | AAPL,NVDA,NKE,GEHC | INDEX再生成 | tickers/AAPL更新 concepts=[macd,fundamental_analysis,sma_divergence] | tickers/AAPL更新 concepts=[fundamental_technical_analysis_integration,social_media_sentiment_analysis,take_profit_strategy] | tickers/AAPL更新 concepts=[take_profit,price_target] | tickers/NVDA更新 concepts=[breakout_trading,earnings_surprise,low_vix_environment] | tickers/NVDA更新 concepts=[stop_loss,entry_strategy] | tickers/NVDA更新 concepts=[breakout,earnings_beat,vix_filtering] | tickers/AAPL更新 concepts=[fundamental_technical_analysis_fusion,social_sentiment_analysis,take_profit_strategy] | tickers/AAPL更新 concepts=[take_profit,target_price] | tickers/NVDA更新 concepts=[breakout,earnings_beat,stop_loss] | tickers/AAPL更新 concepts=[social_hype,take_profit_strategy,fundamental_technical_analysis_combination] | tickers/AAPL更新 concepts=[take_profit,price_target] | tickers/NKE更新 concepts=[social_hype,fundamental_technical_analysis_confirmation,stop_loss_strategy] | tickers/NKE更新 concepts=[stop_loss,entry_criteria] | tickers/GEHC更新 concepts=[composite_signal,social_hype,confirmation_bias_reduction]
2026-05-06 17:00 | INFRA-DIAG | AAPL | インフラ障害診断: CriticAgent Ollama未接続を確認 | 原因=critic_agent.pyハードコードIP(100.105.163.75)+.env重複エントリ | 手動推論実行: OVERRIDE(HOLD)を確認 → フォールバックBUYと乖離 | 修正: critic_agent.py環境変数化+.env重複削除 | concepts/infra_failure_judgment_drift 新規作成
2026-05-07 02:42 | INGEST | AAPL | INDEX再生成 | tickers/AAPL更新 concepts=[thesis_driven_investing,risk_management,llm_based_analysis] | tickers/AAPL更新 concepts=[macd,thesis_driven_trading,fundamental_analysis] | tickers/AAPL更新 concepts=[thesis_driven_trading,news_based_trading,stop_loss]
2026-05-07 02:44 | INGEST | AAPL,NVDA,GEHC,NKE | files=[Log_20260505_AAPL_BUY,Log_20260505_AAPL_BUY_2,Log_20260505_AAPL_BUY_3,Log_20260505_AAPL_BUY_4,Log_20260505_AAPL_SELL,Log_20260505_AAPL_SELL_2,Log_20260505_NVDA_BUY,Log_20260505_NVDA_BUY_2,Log_20260505_NVDA_SELL,Log_20260505_NVDA_SUCCESS,Log_20260506_AAPL_BUY,Log_20260506_AAPL_SELL,Log_20260506_AAPL_SELL_2,Log_20260506_AAPL_SELL_3,Log_20260506_GEHC_BUY,Log_20260506_NKE_BUY,Log_20260506_NKE_SELL] | INDEX再生成 | tickers/AAPL更新 concepts=[fundamental_analysis,social_sentiment,take_profit_strategy] | tickers/AAPL更新 concepts=[social_hype,take_profit_strategy,fundamental_technical_analysis] | tickers/AAPL更新 concepts=[social_hype,fundamental_technical_analysis,take_profit_strategy] | tickers/AAPL更新 concepts=[macd,fundamental_analysis,social_media_sentiment] | tickers/AAPL更新 concepts=[take_profit_strategy,price_target] | tickers/AAPL更新 concepts=[take_profit,price_target] | tickers/NVDA更新 concepts=[breakout,earnings_beat,stop_loss] | tickers/NVDA更新 concepts=[breakout,earnings_surprise,low_vix] | tickers/NVDA更新 concepts=[stop_loss,risk_management,entry_criteria] | tickers/NVDA更新 concepts=[breakout_trading,earnings_play,volatility_filter] | tickers/AAPL更新 concepts=[macd,fundamental_analysis,thesis_driven_trading] | tickers/AAPL更新 concepts=[take_profit,price_target] | tickers/AAPL更新 concepts=[thesis_driven_investing,risk_management,emotional_control] | tickers/AAPL更新 concepts=[thesis_driven_trading,stop_loss_strategy,emotional_control] | tickers/GEHC更新 concepts=[market_sentiment,social_hype,fundamental_technical_analysis_confirmation] | tickers/NKE更新 concepts=[social_hype,stop_loss,fundamental_technical_analysis] | tickers/NKE更新 concepts=[stop_loss,risk_management,entry_strategy]
```
