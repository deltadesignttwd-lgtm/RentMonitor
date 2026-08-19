import os
import json
import time
import requests
import gspread
from datetime import datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ==================== 1. 設定與環境變數 ====================
load_dotenv()

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

# OpenRent SE13 搜尋結果頁面
SEARCH_URL = "https://www.openrent.co.uk/properties-to-rent/london/se13?term=SE13"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# 房源卡片與各欄位的 CSS selector。
# 這是依 OpenRent 常見頁面結構寫的起始版本；此環境無法連到
# openrent.co.uk 做即時驗證，若實跑時抓不到資料，
# 請對照當下的網頁原始碼調整這裡（每個欄位可放多個用逗號分隔的候選 selector）。
LISTING_CARD_SELECTOR = "div.pli"
FIELD_SELECTORS = {
    "address": "h2, .pt-title, a.pli-title",
    "rent_pcm": ".price, .pt-price",
    "property_type": ".property-type, .pt-type, .bedroom-type",
    "furnished": ".furnished-status, .furnished",
    "epc_rating": ".epc-rating, .epc",
    "price_reduced": ".reduced, .price-reduced, .badge-reduced",
}


def _first_text(card, selector_str, default=""):
    for sel in [s.strip() for s in selector_str.split(",")]:
        el = card.select_one(sel)
        if el and el.get_text(strip=True):
            return el.get_text(strip=True)
    return default


def fetch_search_page():
    resp = requests.get(SEARCH_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def parse_listings(html):
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(LISTING_CARD_SELECTOR)

    if not cards:
        print(
            f"⚠️ 找不到任何符合 '{LISTING_CARD_SELECTOR}' 的房源卡片，"
            f"OpenRent 頁面結構可能已變動，請檢查並更新 "
            f"LISTING_CARD_SELECTOR / FIELD_SELECTORS。"
        )
        return []

    listings = []
    for card in cards:
        listings.append({
            "address": _first_text(card, FIELD_SELECTORS["address"], "未提供地址"),
            "rent_pcm": _first_text(card, FIELD_SELECTORS["rent_pcm"], "未提供租金"),
            "property_type": _first_text(card, FIELD_SELECTORS["property_type"], "未提供"),
            "furnished": _first_text(card, FIELD_SELECTORS["furnished"], "未提供"),
            "epc_rating": _first_text(card, FIELD_SELECTORS["epc_rating"], "未提供"),
            "price_reduced": _first_text(card, FIELD_SELECTORS["price_reduced"], "無"),
        })

    return listings


# ==================== 2. Google Sheet 讀寫 ====================
def get_sheet():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        gc = gspread.service_account_from_dict(json.loads(creds_json))
    else:
        gc = gspread.service_account(filename="credentials.json")
    sh = gc.open("SE13_Rent_Tracker")
    return sh.sheet1


def load_existing_listings(worksheet):
    records = worksheet.get_all_records()
    existing = {}
    for idx, row in enumerate(records, start=2):  # 列 1 是標題，資料從列 2 開始
        address = str(row.get("Address", "")).strip().lower()
        if address:
            existing[address] = {
                "row_num": idx,
                "rent": str(row.get("Rent PCM", "")),
            }
    return existing


# ==================== 3. Telegram 發送 ====================
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    res = requests.post(url, json=payload)
    return res.status_code == 200


# ==================== 4. 比對 Google Sheet 並分類 NEW / PRICE DROP ====================
def process_listings(listings):
    worksheet = get_sheet()
    existing_listings = load_existing_listings(worksheet)
    today_str = datetime.now().strftime("%Y-%m-%d")

    new_items = []
    dropped_items = []

    for item in listings:
        address_key = item["address"].strip().lower()
        if not address_key:
            continue

        if address_key not in existing_listings:
            status_tag = "🆕 NEW"
            new_row = [
                f"scrape_{int(time.time() * 1000)}",
                item["address"],
                item["rent_pcm"],
                item["property_type"],
                item["furnished"],
                status_tag,
                today_str,
                today_str,
            ]
            worksheet.append_row(new_row)
            new_items.append(item)
        else:
            old_info = existing_listings[address_key]
            if old_info["rent"] != item["rent_pcm"]:
                status_tag = f"🔻 PRICE DROP (原: {old_info['rent']})"
                worksheet.update_cell(old_info["row_num"], 3, item["rent_pcm"])
                worksheet.update_cell(old_info["row_num"], 6, status_tag)
                worksheet.update_cell(old_info["row_num"], 8, today_str)
                dropped_items.append((item, old_info["rent"]))
            else:
                worksheet.update_cell(old_info["row_num"], 8, today_str)

    return new_items, dropped_items


def build_report(total_count, new_items, dropped_items):
    lines = [
        "🏠 *SE13 租屋監控週報*",
        f"本次共掃描 {total_count} 筆房源，新增 {len(new_items)} 筆，降價 {len(dropped_items)} 筆。",
        "",
    ]

    if new_items:
        lines.append("🆕 *新房源*")
        for item in new_items:
            lines.append(
                f"📍 {item['address']}\n"
                f"💰 {item['rent_pcm']} | 🛏️ {item['property_type']} | "
                f"🛋️ {item['furnished']} | ⚡ EPC {item['epc_rating']}"
            )
        lines.append("")

    if dropped_items:
        lines.append("🔻 *降價房源*")
        for item, old_rent in dropped_items:
            lines.append(f"📍 {item['address']}\n💰 {old_rent} → {item['rent_pcm']}")
        lines.append("")

    if not new_items and not dropped_items:
        lines.append("本週沒有新房源或降價，維持觀察。")

    lines.append(f"📅 更新時間: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    return "\n".join(lines)


# ==================== 5. 主程式 ====================
def main():
    print("🤖 開始抓取 OpenRent SE13 房源...")
    html = fetch_search_page()
    listings = parse_listings(html)
    print(f"共抓到 {len(listings)} 筆房源。")

    if not listings:
        send_telegram("⚠️ SE13 租屋監控：本次未抓到任何房源，請確認 OpenRent 頁面結構是否變動。")
        return

    new_items, dropped_items = process_listings(listings)
    report = build_report(len(listings), new_items, dropped_items)

    if send_telegram(report):
        print("🎉 Telegram 通報發送成功！")
    else:
        print("❌ Telegram 發送失敗。")


if __name__ == "__main__":
    main()
