import time
import feedparser
import urllib.parse
import requests
import json
import warnings
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

warnings.filterwarnings('ignore')
load_dotenv()

# ==========================================
# 📱 LINE Botの設定
# ==========================================
LINE_ACCESS_TOKEN = "rLmNKB5qoOYjlQ1W7G46SpD2dhH3uxCNxqHYnyqWKTmPRWGPP0ZpqrfWs8y3MRFXym3ctwIZXlC14eo2LxXjx++Hha4Fgy2RJX1Ii1LCuRuThgkshqMko1DHIgbDrm812uX+2ywiI6vA9GuJiBy3pAdB04t89/1O/w1cDnyilFU="
LINE_USER_ID = "U266575a29b79da182dfad34f6e879603"

def send_line_message(text):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"
    }
    data = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": text}]}
    requests.post(url, headers=headers, data=json.dumps(data))

# ==========================================
# 📰 監視設定
# ==========================================
COMPANIES = ["エクサウィザーズ", "QDレーザ", "三菱重工"]
CHECK_INTERVAL_SECONDS = 900  # 15分おきにチェック（900秒）

seen_urls = set()

# ==========================================
# 🤖 AIニュース監視ループ
# ==========================================
def monitor_news_loop():
    print("🟢 【監視スタート】AIエージェントが24時間監視を開始しました！(停止は Ctrl+C)\n")
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)

    while True:
        current_time = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{current_time}] 最新ニュースをパトロール中...")

        for company in COMPANIES:
            query = urllib.parse.quote(company)
            url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
            feed = feedparser.parse(url)

            if not feed.entries:
                continue

            latest_news = feed.entries[0]
            title = latest_news.title
            link = latest_news.link

            if link not in seen_urls:
                print(f"  🚨 新着記事発見！ [{company}]: {title}")
                print(f"  -> AIに株価への影響を判定させています...")

                prompt = f"""
                あなたはプロの株式投資アナリストです。
                以下の対象企業に関する最新ニュースのタイトルを読み、株価に対する影響を分析してください。

                【対象企業】: {company}
                【ニュースタイトル】: {title}

                以下の形式で簡潔に出力してください。
                【判定】ポジティブ / ネガティブ / 中立 のいずれか
                【理由】投資家目線での理由を1〜2文で。
                """
                
                analysis = llm.invoke(prompt).content

                message = f"📰 【AI監視速報: {company}】\n\n■ 記事:\n{title}\n\n{analysis}\n\n■ リンク:\n{link}"
                send_line_message(message)
                
                seen_urls.add(link)
                print(f"  -> ✅ LINEへ速報を送信完了！")
                
                # API制限回避のためのクールダウン
                print(f"  -> ⏳ API制限回避のため、10秒間待機します...")
                time.sleep(10)

        print(f"-> パトロール完了。次の巡回まで {CHECK_INTERVAL_SECONDS/60}分 待機します...\n")
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    monitor_news_loop()
