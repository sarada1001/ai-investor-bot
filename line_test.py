import requests
import json

# ==========================================
# ① 発行した「チャネルアクセストークン（長期）」を貼り付けます
ACCESS_TOKEN = "rLmNKB5qoOYjlQ1W7G46SpD2dhH3uxCNxqHYnyqWKTmPRWGPP0ZpqrfWs8y3MRFXym3ctwIZXlC14eo2LxXjx++Hha4Fgy2RJX1Ii1LCuRuThgkshqMko1DHIgbDrm812uX+2ywiI6vA9GuJiBy3pAdB04t89/1O/w1cDnyilFU="

# ② あなたの「ユーザーID（Uから始まる文字列）」を貼り付けます
USER_ID = "U266575a29b79da182dfad34f6e879603"
# ==========================================

def send_line_message(text):
    """LINEのMessaging APIを使ってメッセージを送信する関数"""
    url = "https://api.line.me/v2/bot/message/push"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ACCESS_TOKEN}"
    }
    
    data = {
        "to": USER_ID,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }
    
    # LINEのサーバーにデータを送信（POSTリクエスト）
    response = requests.post(url, headers=headers, data=json.dumps(data))
    
    # 結果の確認
    if response.status_code == 200:
        print("✅ LINEへの送信に成功しました！スマホを確認してください。")
    else:
        print(f"❌ エラーが発生しました: {response.status_code}")
        print(response.text)

# テスト実行
if __name__ == "__main__":
    test_message = "🤖 AIリサーチャーの稼働テストです！\n\nこれが届けば、システムとの連携は完璧です。"
    send_line_message(test_message)
