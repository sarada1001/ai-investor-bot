import requests
import json
import os
import glob
from datetime import datetime

# === 設定エリア ===
DESKTOP_IP = "100.105.163.75"
OLLAMA_URL = f"http://{DESKTOP_IP}:11434/api/generate"

# クラウド環境でのプロジェクトルート
PROJECT_ROOT = "/home/naito/exa-investor"

# 保存先: Obsidian用フォルダ (後で手元のPCと同期)
VAULT_PATH = os.path.join(PROJECT_ROOT, "docs")
MODEL = "llama3.1"

def call_desktop_ai(prompt):
    system_context = (
        "あなたは情報計算科学専攻の学生をサポートする投資AI『exa-investor』の開発研究員です。"
        "入力されたJSONログを解析し、以下の情報をMarkdown形式で要約してください。\n"
        "1. 今日のTop3選出銘柄とその理由（RSIなどのスコア）\n"
        "2. 各専門エージェント（Fundamental, Technical等）の推論の要点（CoT）\n"
        "3. ManagerAgentの最終判断（Buy/Sell/Hold）とAlpacaへの注文結果\n"
        "出力は、そのままObsidianに追記できる形式にしてください。"
    )
    
    payload = {
        "model": MODEL,
        "prompt": f"{system_context}\n\n【ログ内容】\n{prompt}",
        "stream": False
    }
    
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=60)
        return response.json().get('response', 'Error: No response')
    except Exception as e:
        return f"通信エラーが発生しました: {str(e)}"

def save_to_obsidian(filename, content):
    if not os.path.exists(VAULT_PATH):
        os.makedirs(VAULT_PATH)
    full_path = os.path.join(VAULT_PATH, filename)
    with open(full_path, "a", encoding="utf-8") as f:
        f.write(content)

def get_latest_file(directory_path, pattern):
    """指定ディレクトリ内の最新のファイルパスを取得する"""
    search_pattern = os.path.join(directory_path, pattern)
    files = glob.glob(search_pattern)
    if not files:
        return None
    # 更新日時が最も新しいものを取得
    latest_file = max(files, key=os.path.getctime)
    return latest_file

def main():
    print("最新のログファイルを検索しています...")
    
    # 1. 最新の推論ログ (bbs/*.json) を取得
    bbs_dir = os.path.join(PROJECT_ROOT, "bbs")
    latest_bbs_file = get_latest_file(bbs_dir, "*.json")
    
    # 2. 最新のスクリーナーログ (data/screener/*.json) を取得
    screener_dir = os.path.join(PROJECT_ROOT, "data", "screener")
    latest_screener_file = get_latest_file(screener_dir, "*.json")

    if not latest_bbs_file:
        print("推論ログ (bbs/*.json) が見つかりませんでした。")
        return

    # ログ内容の結合
    combined_log_data = f"--- 推論ログ ({os.path.basename(latest_bbs_file)}) ---\n"
    with open(latest_bbs_file, "r", encoding="utf-8") as f:
        # トークン節約のため、マネージャー判断部分など必要な箇所だけ抽出する処理を入れるとさらに良いです
        combined_log_data += f.read()

    if latest_screener_file:
         combined_log_data += f"\n\n--- スクリーナーログ ({os.path.basename(latest_screener_file)}) ---\n"
         with open(latest_screener_file, "r", encoding="utf-8") as f:
             combined_log_data += f.read()

    print("🚀 デスクトップPCのAIにログを解析させています...")
    
    # 3. AIによる要約
    analysis_result = call_desktop_ai(combined_log_data)
    
    # 4. Obsidian用のファイル形式にフォーマットして保存
    today = datetime.now().strftime("%Y-%m-%d")
    formatted_content = f"\n---\n### 📅 {today} 実行ログ解析\n\n{analysis_result}\n\n#exa_investor #experiment_log\n"
    
    save_to_obsidian("exa_investor_logs.md", formatted_content)
    
    print(f"✅ クラウド上の '{VAULT_PATH}/exa_investor_logs.md' を更新しました！")

if __name__ == "__main__":
    main()