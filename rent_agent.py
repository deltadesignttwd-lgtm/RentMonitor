import os
import json
import requests
import gspread
from datetime import datetime
from anthropic import Anthropic
from dotenv import load_dotenv

# ==================== 1. 設定與環境變數 ====================
load_dotenv()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "your_claude_api_key_here")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "your_telegram_bot_token_here")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "your_telegram_chat_id_here")

client = Anthropic(api_key=ANTHROPIC_API_KEY)

# ==================== 2. 工具函數：Google Sheet 讀寫 ====================
def get_sheet():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        gc = gspread.service_account_from_dict(json.loads(creds_json))
    else:
        gc = gspread.service_account(filename="credentials.json")
    sh = gc.open("SE13_Rent_Tracker")
    return sh.sheet1

def load_existing_listings(worksheet):
    # 讀取所有資料（包含 Header）
    records = worksheet.get_all_records()
    existing = {}
    for idx, row in enumerate(records, start=2): # 列 1 是標題，資料從列 2 開始
        address = str(row.get("Address", "")).strip().lower()
        if address:
            existing[address] = {
                "row_num": idx,
                "rent": str(row.get("Rent PCM", "")),
                "status": str(row.get("Status", ""))
            }
    return existing

# ==================== 3. 工具函數：Telegram 發送 ====================
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    res = requests.post(url, json=payload)
    return res.status_code == 200

# ==================== 4. Agent 主邏輯 ====================
def process_rent_agent(raw_text):
    print("🤖 Agent 正在解析 OpenRent 房源內容...")

    # A. 呼叫 Claude 解析結構化資料
    prompt = f"""
    你是一個租屋資料提取助手。請幫我解析以下這段 OpenRent 房源文字，並嚴格回傳一個 JSON 物件（不要包含任何額外解說文字或 Markdown 標籤）：

    JSON 結構需求：
    {{
      "address": "地址描述 (例如 Lee High Road, SE13)",
      "rent_pcm": "每月租金 (例如 £1,595)",
      "property_type": "房型 (例如 1 Bed Flat)",
      "epc_rating": "EPC 評級 (若無則寫 '未提供')",
      "furnished": "Furnished 或 Unfurnished",
      "price_reduced": "降價描述 (若無寫 '無')"
    }}

    原始文字：
    {raw_text}
    """

    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )

    try:
        data_text = response.content[0].text.strip()
        # 清理可能夾帶的 ```json 標記
        if data_text.startswith("```"):
            data_text = data_text.split("\n", 1)[1].rsplit("\n", 1)[0]
        parsed_data = json.loads(data_text)
    except Exception as e:
        print(f"❌ 解析 JSON 失敗: {e}")
        return

    # B. 比對 Google Sheet 歷史紀錄
    worksheet = get_sheet()
    existing_listings = load_existing_listings(worksheet)

    address_key = parsed_data["address"].strip().lower()
    today_str = datetime.now().strftime("%Y-%m-%d")

    status_tag = ""
    if address_key not in existing_listings:
        # 新房源
        status_tag = "🆕 NEW"
        new_row = [
            f"link_{int(datetime.now().timestamp())}",
            parsed_data["address"],
            parsed_data["rent_pcm"],
            parsed_data["property_type"],
            parsed_data["furnished"],
            status_tag,
            today_str,
            today_str
        ]
        worksheet.append_row(new_row)
        print("✅ 已將新房源紀錄至 Google Sheet。")
    else:
        # 已有舊紀錄 -> 比對價格
        old_info = existing_listings[address_key]
        old_rent = old_info["rent"]

        if old_rent != parsed_data["rent_pcm"]:
            status_tag = f"🔻 PRICE DROP (原: {old_rent})"
            worksheet.update_cell(old_info["row_num"], 3, parsed_data["rent_pcm"]) # 更新租金
            worksheet.update_cell(old_info["row_num"], 6, status_tag)              # 更新狀態
            worksheet.update_cell(old_info["row_num"], 8, today_str)              # 更新時間
            print("✅ 已更新 Google Sheet 降價資訊。")
        else:
            status_tag = "⚡ UNCHANGED"
            worksheet.update_cell(old_info["row_num"], 8, today_str)
            print("✅ 房源無變化，已更新 Last Updated 時間。")

    # C. 組合 Telegram 報告並發送
    tg_report = (
        f"🏠 *SE13 5HU 租屋監控週報*\n"
        f"狀態：*{status_tag}*\n\n"
        f"📍 *地址*: {parsed_data['address']}\n"
        f"💰 *租金*: {parsed_data['rent_pcm']}\n"
        f"🛏️ *房型*: {parsed_data['property_type']}\n"
        f"🛋️ *傢俱*: {parsed_data['furnished']}\n"
        f"⚡ *EPC*: {parsed_data['epc_rating']}\n"
        f"📉 *降價備註*: {parsed_data['price_reduced']}\n\n"
        f"📅 更新時間: {today_str}"
    )

    if send_telegram(tg_report):
        print("🎉 Telegram 通報發送成功！")
    else:
        print("❌ Telegram 發送失敗。")

# ==================== 5. 測試執行 ====================
if __name__ == "__main__":
    sample_text = """
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
    process_rent_agent(sample_text)
