import os
import requests

# Set these as environment variables before running, e.g.:
#   export TG_BOT_TOKEN="your-bot-token"
#   export TG_CHAT_ID="your-chat-id"
TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_CHAT_ID = os.environ["TG_CHAT_ID"]

# Example OpenRent listing text (copied from the site for testing)
raw_listing_text = """
£1,595
per month

0.53
km

Last updated around 4 hours ago
1 Bed Flat, Lee High Road, SE13
We are proud to offer this gorgeous 1 bedroom, 1 bathroom GARDEN FLAT in a great location. Available to move in from Wedn ...1 Bed
1 Bath
Unfurnished
"""


def format_and_send():
    message = (
        "🏠 *SE13 5HU 租房監控測試*\n\n"
        "📍 *地址*: Lee High Road, SE13 (距 SE13 5HU 約 0.53 km)\n"
        "💰 *租金*: £1,595 / month\n"
        "🛏️ *房型*: 1 Bed Flat (1 Bath)\n"
        "🛋️ *傢俱*: Unfurnished\n"
        "📉 *降價狀態*: 暫無資訊\n"
        "⚡ *EPC*: 待詳情頁確認\n\n"
        "✅ *Agent 第一版測試 successfully sent!*"
    )

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }

    response = requests.post(url, json=payload)
    if response.status_code == 200:
        print("🎉 成功！請檢查你的 Telegram 機器人。")
    else:
        print(f"❌ 發送失敗，錯誤碼：{response.status_code}")
        print(response.text)


if __name__ == "__main__":
    format_and_send()
