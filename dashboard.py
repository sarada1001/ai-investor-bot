# dashboard.py
import streamlit as st
import json
import glob
import os

# ページ全体のデザイン設定（ダークモードが似合うワイドレイアウト）
st.set_page_config(page_title="ECC Autonomous Fund", page_icon="🏦", layout="wide")

st.title("🏦 ECC 自律型AIファンド 運用ダッシュボード")
st.markdown("---")

# 📊 上部パネル：ポートフォリオの健康診断（フェーズ0の可視化）
st.subheader("📊 現在のポートフォリオ状況 (Mock)")
col1, col2, col3, col4 = st.columns(4)

# metricを使うと、プロっぽい増減表示が簡単に作れます
col1.metric(label="リスク許容額", value="$2,000.00", delta="稼働中")
col2.metric(label="保有銘柄 [AAPL]", value="$182.00", delta="+4.00%")
# TSLAはマイナスなので、delta_color="inverse" で赤色表示にする
col3.metric(label="保有銘柄 [TSLA]", value="$205.00", delta="-2.38%", delta_color="inverse")
col4.metric(label="システム状態", value="✅ 正常", delta="S&P500監視中", delta_color="off")

st.markdown("---")
st.subheader("📝 AI投資会議 議事録ビューア")

# bbsディレクトリからJSONファイルを新しい順に取得
log_files = sorted(glob.glob("bbs/*.json"), reverse=True)

if not log_files:
    st.warning("BBSディレクトリにログがありません。パイプラインを実行してください。")
    st.stop()

# 📂 サイドバー（左側のメニュー）に選択項目を配置してスッキリさせる
st.sidebar.header("📁 セッション履歴")
if st.sidebar.button("🔄 最新のログを取得"):
    st.rerun()
selected_file = st.sidebar.selectbox("確認したい会議ログを選択", log_files)



try:
    with open(selected_file, 'r', encoding='utf-8') as f:
        log_data = json.load(f)
    
    st.caption(f"表示中のログファイル: {selected_file}")

    # 🎯 ManagerAgentの最終決断を一番上に大きく表示
    if 'manager_judgment' in log_data:
        st.success("🎯 **ManagerAgent 最終決断パネル**")
        # st.codeを使うと、等幅フォントでカッコよく表示されます
        st.code(log_data['manager_judgment'], language="plaintext")

    st.markdown("### 🔍 各エージェントの分析レポート")
    
    # 他のレポートを「アコーディオン形式（折りたたみ）」でスタイリッシュに表示
    for agent, report in log_data.items():
        if agent != 'manager_judgment':
            # 変数名（technical_analysis等）をタイトル風に整形
            display_name = agent.replace('_', ' ').title()
            with st.expander(f"🤖 {display_name} のレポートを見る"):
                st.write(report)
                
except Exception as e:
    st.error(f"ログの読み込みに失敗しました: {e}")