import os
import requests
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("APCA_API_KEY_ID")
secret_key = os.getenv("APCA_API_SECRET_KEY")

url = "https://paper-api.alpaca.markets/v2/account"
headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}

response = requests.get(url, headers=headers)
if response.status_code == 200:
    print("✅ 接続成功！口座ステータス:", response.json().get("status"))
    print("💰 現在のBuying Power:", response.json().get("buying_power"), "USD")
else:
    print("❌ 接続エラー:", response.status_code, response.text)
