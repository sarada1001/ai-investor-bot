# 投資ナレッジベース INDEX

> 最終更新: 2026-05-07 02:44
> 管理スクリプト: `python server_librarian.py --ingest`

## 現在の投資方針

AIエージェントの直近判断をもとに自動生成。詳細は各ティッカーページを参照。

## 保有ポジション

現在、保有ポジションはありません。

## ティッカー別ページ

- [[tickers/AAPL]] — Apple Inc. | HOLD | 2026-05-07
- [[tickers/GEHC]] — GE HealthCare Technologies Inc. | BUY | 2026-05-07
- [[tickers/NKE]] — NIKE, Inc. | HOLD | 2026-05-07
- [[tickers/NVDA]] — NVIDIA Corporation | BUY | 2026-05-07

## コンセプトページ

- [[concepts/breakout]] — ブレイクアウト
- [[concepts/breakout]] — ブレイクアウト取引
- [[concepts/composite_signal]] — 複合シグナル
- [[concepts/confirmation_bias_reduction]] — 確証バイアス軽減
- [[concepts/earnings_beat]] — 好決算
- [[concepts/earnings_beat]] — 決算プレイ
- [[concepts/earnings_beat]] — 決算サプライズ
- [[concepts/emotional_control]] — 感情コントロール
- [[concepts/entry_strategy]] — エントリー条件
- [[concepts/entry_strategy]] — エントリー戦略
- [[concepts/fundamental_technical_analysis]] — ファンダメンタル分析
- [[concepts/fundamental_technical_analysis]] — ファンダメンタルズ・テクニカル分析
- [[concepts/fundamental_technical_analysis]] — ファンダメンタル・テクニカル分析の組み合わせ
- [[concepts/fundamental_technical_analysis]] — ファンダメンタルズ・テクニカル分析の確認
- [[concepts/fundamental_technical_analysis]] — ファンダメンタル・テクニカル分析の融合
- [[concepts/fundamental_technical_analysis]] — ファンダメンタル・テクニカル分析の統合
- [[concepts/infra_failure_judgment_drift]] — インフラ障害による判断乖離リスク
- [[concepts/llm_based_analysis]] — LLMに基づく分析
- [[concepts/low_vix]] — 低ボラティリティ環境
- [[concepts/low_vix]] — 低VIX環境
- [[concepts/macd]] — MACD (移動平均収束拡散法)
- [[concepts/market_sentiment]] — 市場センチメント
- [[concepts/news_based_trading]] — ニュースに基づく取引
- [[concepts/price_target]] — 目標株価設定
- [[concepts/risk_management]] — リスク管理
- [[concepts/sma_divergence]] — SMA乖離
- [[concepts/social_hype]] — SNS煽り
- [[concepts/social_sentiment]] — SNSセンチメント分析
- [[concepts/social_sentiment]] — ソーシャルメディアセンチメント分析
- [[concepts/social_sentiment]] — SNSセンチメント
- [[concepts/social_sentiment]] — ソーシャルセンチメント分析
- [[concepts/stop_loss]] — ストップロス
- [[concepts/stop_loss]] — ストップロス戦略
- [[concepts/take_profit]] — 利確戦略
- [[concepts/take_profit]] — 利益確定戦略
- [[concepts/price_target]] — 目標株価設定
- [[concepts/thesis_driven_trading]] — 仮説駆動投資
- [[concepts/thesis_driven_trading]] — セシスドリブントレード
- [[concepts/vix_filtering]] — VIXフィルタリング
- [[concepts/vix_filtering]] — ボラティリティフィルタ

## 最近の Ingest 履歴

```
2026-05-06 13:56 | INGEST | AAPL,NVDA,NKE,GEHC | INDEX再生成 | tickers/AAPL更新 concepts=[macd,fundamental_analysis,sma_divergence] | tickers/AAPL更新 concepts=[fundamental_technical_analysis_integration,social_media_sentiment_analysis,take_profit_strategy] | tickers/AAPL更新 concepts=[take_profit,price_target] | tickers/NVDA更新 concepts=[breakout_trading,earnings_surprise,low_vix_environment] | tickers/NVDA更新 concepts=[stop_loss,entry_strategy] | tickers/NVDA更新 concepts=[breakout,earnings_beat,vix_filtering] | tickers/AAPL更新 concepts=[fundamental_technical_analysis_fusion,social_sentiment_analysis,take_profit_strategy] | tickers/AAPL更新 concepts=[take_profit,target_price] | tickers/NVDA更新 concepts=[breakout,earnings_beat,stop_loss] | tickers/AAPL更新 concepts=[social_hype,take_profit_strategy,fundamental_technical_analysis_combination] | tickers/AAPL更新 concepts=[take_profit,price_target] | tickers/NKE更新 concepts=[social_hype,fundamental_technical_analysis_confirmation,stop_loss_strategy] | tickers/NKE更新 concepts=[stop_loss,entry_criteria] | tickers/GEHC更新 concepts=[composite_signal,social_hype,confirmation_bias_reduction]
2026-05-06 17:00 | INFRA-DIAG | AAPL | インフラ障害診断: CriticAgent Ollama未接続を確認 | 原因=critic_agent.pyハードコードIP(100.105.163.75)+.env重複エントリ | 手動推論実行: OVERRIDE(HOLD)を確認 → フォールバックBUYと乖離 | 修正: critic_agent.py環境変数化+.env重複削除 | concepts/infra_failure_judgment_drift 新規作成
2026-05-07 02:42 | INGEST | AAPL | INDEX再生成 | tickers/AAPL更新 concepts=[thesis_driven_investing,risk_management,llm_based_analysis] | tickers/AAPL更新 concepts=[macd,thesis_driven_trading,fundamental_analysis] | tickers/AAPL更新 concepts=[thesis_driven_trading,news_based_trading,stop_loss]
```
