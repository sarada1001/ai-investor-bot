# 投資ナレッジベース INDEX

> 最終更新: 2026-05-17 17:46
> 管理スクリプト: `python server_librarian.py --ingest`

## 現在の投資方針

AIエージェントの直近判断をもとに自動生成。詳細は各ティッカーページを参照。

## 保有ポジション

現在、保有ポジションはありません。

## ティッカー別ページ

- [[tickers/AAPL]] — Apple Inc. | HOLD | 2026-05-11
- [[tickers/COR]] — Cencora, Inc. | BUY | 2026-05-11
- [[tickers/GEHC]] — GE HealthCare Technologies Inc. | BUY | 2026-05-07
- [[tickers/HSY]] — The Hershey Company | BUY | 2026-05-08
- [[tickers/IP]] — International Paper Company | BUY | 2026-05-17
- [[tickers/NKE]] — NIKE, Inc. | HOLD | 2026-05-07
- [[tickers/NVDA]] — NVIDIA Corporation | BUY | 2026-05-11
- [[tickers/RTX]] — RTX Corporation | BUY | 2026-05-17
- [[tickers/TEST]] — TEST | WATCH | 2026-05-11

## コンセプトページ

- [[concepts/Weekly_Reflection_2026-05-08]] — 週次反省会 2026-05-08
- [[concepts/atr]] — ATR (Average True Range)
- [[concepts/atr_based_take_profit]] — ATRベースの利確
- [[concepts/atr_based_target_price]] — ATRベース目標株価
- [[concepts/breakout]] — ブレイクアウト
- [[concepts/composite_signal]] — 複合シグナル
- [[concepts/confirmation_bias_reduction]] — 確証バイアス軽減
- [[concepts/earnings_beat]] — 好決算・決算プレイ
- [[concepts/emotional_control]] — 感情コントロール
- [[concepts/entry_strategy]] — エントリー戦略・条件
- [[concepts/fundamental_analysis]] — ファンダメンタルズ分析
- [[concepts/fundamental_technical_analysis]] — ファンダメンタルズ・テクニカル分析
- [[concepts/fundamental_technical_analysis_combination]] — ファンダメンタルズ・テクニカル分析の組み合わせ
- [[concepts/fundamental_technical_analysis_confirmation]] — ファンダメンタルズ・テクニカル分析確認
- [[concepts/fundamental_technical_analysis_fusion]] — ファンダメンタルズ・テクニカル分析の融合
- [[concepts/infra_failure_judgment_drift]] — インフラ障害による判断乖離リスク
- [[concepts/llm_based_analysis]] — LLMに基づく分析
- [[concepts/low_vix]] — 低ボラティリティ環境
- [[concepts/macd]] — MACD (移動平均収束拡散法)
- [[concepts/market_sentiment]] — 市場センチメント
- [[concepts/market_sentiment_analysis]] — 市場センチメント分析
- [[concepts/multi_signal_score]] — 複合シグナルスコア
- [[concepts/multiple_factors_analysis]] — 複合的要因分析
- [[concepts/news_based_trading]] — ニュースに基づく取引
- [[concepts/percentage_based_profit]] — パーセンテージベースの利益
- [[concepts/predefined_target_price]] — 目標株価設定
- [[concepts/price_target]] — 目標株価設定
- [[concepts/profit_taking_strategy]] — 利益確定戦略
- [[concepts/risk_management]] — リスク管理
- [[concepts/risk_on_attitude]] — リスクオン姿勢
- [[concepts/risk_on_market]] — リスクオン市場
- [[concepts/rule_based_trading]] — ルールベース取引
- [[concepts/short_term_trend_following]] — 短期トレンドフォロー
- [[concepts/sma_divergence]] — SMA乖離
- [[concepts/sns_sentiment]] — SNSセンチメント
- [[concepts/sns_sentiment_analysis]] — SNSセンチメント分析
- [[concepts/sns_sentiment_risk]] — SNSセンチメントリスク
- [[concepts/social_hype]] — SNS煽り
- [[concepts/social_hype_consideration]] — SNSの煽りの考慮
- [[concepts/social_hype_factor]] — SNS煽り要因
- [[concepts/social_media_hype]] — SNS煽り
- [[concepts/social_media_sentiment_analysis]] — ソーシャルメディアセンチメント分析
- [[concepts/social_sentiment]] — SNSセンチメント
- [[concepts/social_sentiment_analysis]] — ソーシャルセンチメント分析
- [[concepts/stop_loss]] — ストップロス
- [[concepts/take_profit]] — 利確戦略
- [[concepts/take_profit_strategy]] — 利確戦略
- [[concepts/technical_analysis]] — テクニカル分析
- [[concepts/thesis_driven_trading]] — テーゼドリブン取引
- [[concepts/vix_filtering]] — VIXフィルタリング

## 最近の Ingest 履歴

```
2026-05-06 17:00 | INFRA-DIAG | AAPL | インフラ障害診断: CriticAgent Ollama未接続を確認 | 原因=critic_agent.pyハードコードIP(100.105.163.75)+.env重複エントリ | 手動推論実行: OVERRIDE(HOLD)を確認 → フォールバックBUYと乖離 | 修正: critic_agent.py環境変数化+.env重複削除 | concepts/infra_failure_judgment_drift 新規作成
2026-05-07 02:42 | INGEST | AAPL | INDEX再生成 | tickers/AAPL更新 concepts=[thesis_driven_investing,risk_management,llm_based_analysis] | tickers/AAPL更新 concepts=[macd,thesis_driven_trading,fundamental_analysis] | tickers/AAPL更新 concepts=[thesis_driven_trading,news_based_trading,stop_loss]
2026-05-07 02:44 | INGEST | AAPL,NVDA,GEHC,NKE | files=[Log_20260505_AAPL_BUY,Log_20260505_AAPL_BUY_2,Log_20260505_AAPL_BUY_3,Log_20260505_AAPL_BUY_4,Log_20260505_AAPL_SELL,Log_20260505_AAPL_SELL_2,Log_20260505_NVDA_BUY,Log_20260505_NVDA_BUY_2,Log_20260505_NVDA_SELL,Log_20260505_NVDA_SUCCESS,Log_20260506_AAPL_BUY,Log_20260506_AAPL_SELL,Log_20260506_AAPL_SELL_2,Log_20260506_AAPL_SELL_3,Log_20260506_GEHC_BUY,Log_20260506_NKE_BUY,Log_20260506_NKE_SELL] | INDEX再生成 | tickers/AAPL更新 concepts=[fundamental_analysis,social_sentiment,take_profit_strategy] | tickers/AAPL更新 concepts=[social_hype,take_profit_strategy,fundamental_technical_analysis] | tickers/AAPL更新 concepts=[social_hype,fundamental_technical_analysis,take_profit_strategy] | tickers/AAPL更新 concepts=[macd,fundamental_analysis,social_media_sentiment] | tickers/AAPL更新 concepts=[take_profit_strategy,price_target] | tickers/AAPL更新 concepts=[take_profit,price_target] | tickers/NVDA更新 concepts=[breakout,earnings_beat,stop_loss] | tickers/NVDA更新 concepts=[breakout,earnings_surprise,low_vix] | tickers/NVDA更新 concepts=[stop_loss,risk_management,entry_criteria] | tickers/NVDA更新 concepts=[breakout_trading,earnings_play,volatility_filter] | tickers/AAPL更新 concepts=[macd,fundamental_analysis,thesis_driven_trading] | tickers/AAPL更新 concepts=[take_profit,price_target] | tickers/AAPL更新 concepts=[thesis_driven_investing,risk_management,emotional_control] | tickers/AAPL更新 concepts=[thesis_driven_trading,stop_loss_strategy,emotional_control] | tickers/GEHC更新 concepts=[market_sentiment,social_hype,fundamental_technical_analysis_confirmation] | tickers/NKE更新 concepts=[social_hype,stop_loss,fundamental_technical_analysis] | tickers/NKE更新 concepts=[stop_loss,risk_management,entry_strategy]
2026-05-08 08:17 | INGEST | AAPL,HSY | files=[Log_20260505_AAPL_BUY_5,Log_20260505_AAPL_SELL_3,Log_20260507_AAPL_BUY,Log_20260507_AAPL_BUY_2,Log_20260507_AAPL_BUY_3,Log_20260507_HSY_BUY] | INDEX再生成 | tickers/AAPL更新 concepts=[macd,fundamental_analysis,market_sentiment] | tickers/AAPL更新 concepts=[take_profit_strategy,predefined_target_price] | tickers/AAPL更新 concepts=[market_sentiment,fundamental_technical_analysis,social_media_hype] | tickers/AAPL更新 concepts=[market_sentiment,fundamental_analysis,technical_analysis] | tickers/AAPL更新 concepts=[market_sentiment_analysis,social_hype_factor] | tickers/HSY更新 concepts=[fundamental_analysis,sns_sentiment,risk_on_market]
2026-05-11 23:05 | INGEST | AAPL,NVDA,TEST,COR | files=[Log_20260509_AAPL_BUY,Log_20260509_NVDA_BUY,Log_20260509_TEST_SKIPPED,Log_20260511_AAPL_BUY,Log_20260511_AAPL_BUY_2,Log_20260511_AAPL_BUY_3,Log_20260511_AAPL_BUY_4,Log_20260511_AAPL_BUY_5,Log_20260511_AAPL_BUY_6,Log_20260511_AAPL_SELL,Log_20260511_AAPL_SELL_2,Log_20260511_AAPL_SELL_3,Log_20260511_AAPL_SELL_4,Log_20260511_AAPL_SELL_5,Log_20260511_COR_BUY] | INDEX再生成 | tickers/AAPL更新 concepts=[fundamental_analysis,sns_sentiment_analysis,risk_on_attitude] | tickers/NVDA更新 concepts=[multiple_factors_analysis,sns_sentiment_risk,risk_management] | tickers/TEST更新 concepts=[risk_management,profit_taking_strategy,rule_based_trading] | tickers/AAPL更新 concepts=[fundamental_technical_analysis_fusion,take_profit_strategy,social_media_sentiment_analysis] | tickers/AAPL更新 concepts=[fundamental_technical_analysis_combination,take_profit_strategy,social_hype_consideration] | tickers/AAPL更新 concepts=[social_hype,fundamental_technical_analysis_confirmation,atr_based_take_profit] | tickers/AAPL更新 concepts=[social_sentiment_analysis,take_profit_strategy,multi_signal_score] | tickers/AAPL更新 concepts=[social_hype,fundamental_technical_analysis_confirmation,atr_based_take_profit] | tickers/AAPL更新 concepts=[social_sentiment,fundamental_analysis,technical_analysis] | tickers/AAPL更新 concepts=[take_profit,atr_based_target_price] | tickers/AAPL更新 concepts=[atr_based_take_profit,rule_based_trading] | tickers/AAPL更新 concepts=[take_profit,atr] | tickers/AAPL更新 concepts=[atr_based_target_price,take_profit_strategy] | tickers/AAPL更新 concepts=[atr_based_take_profit,rule_based_trading,percentage_based_profit] | tickers/COR更新 concepts=[fundamental_analysis,social_media_hype,profit_taking_strategy]
```
