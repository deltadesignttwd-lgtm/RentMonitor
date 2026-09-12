import os
import re
import json
import requests
import gspread
from datetime import datetime
from dotenv import load_dotenv
from urllib.parse import urljoin

# ==================== 1. 設定與環境變數 ====================
load_dotenv()

GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
SHEET_NAME = "Rent Monitor"

# 只寫入「地址」符合 ADDRESS_FILTERS 其中一個關鍵字、房間數等於 BEDROOMS_FILTER、
# 且房型屬於 PROPERTY_TYPE_FILTER 其中之一的房源。
ADDRESS_FILTERS = ["Eastdown Park", "Dermody Road", "Wisteria Road", "Gilmore Road", "Lee High Road"]
BEDROOMS_FILTER = 1
PROPERTY_TYPE_FILTER = ["Flat", "Apartment"]

# Rightmove SE13 5HU 搜尋結果頁面，1 房、0.25 英里範圍
SEARCH_URL = (
    "https://www.rightmove.co.uk/property-to-rent/find.html"
    "?searchLocation=SE13+5HU&useLocationIdentifier=true"
    "&locationIdentifier=POSTCODE%5E755741&radius=0.25"
    "&minBedrooms=1&maxBedrooms=1&_includeLetAgreed=on"
)

# 跟 scrape_zoopla.py 一樣：Accept-Encoding 不列 "br"，避免 Brotli 壓縮內容
# 沒裝套件解不開、resp.text 變亂碼的問題。
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.rightmove.co.uk/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}

# Rightmove 頁面是 Next.js SSR，房源資料完整內嵌在
# <script id="__NEXT_DATA__" type="application/json">，包含 bedrooms、
# propertySubType（Flat/Apartment/House Share...）、displayAddress、price 等
# 乾淨欄位，不需要用容易隨改版失效的 CSS class 去猜。
NEXT_DATA_PATTERN = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL
)


def fetch_search_page():
    resp = requests.get(SEARCH_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def parse_listings(html):
    match = NEXT_DATA_PATTERN.search(html)
    if not match:
        print(
            "⚠️ 找不到 __NEXT_DATA__ 結構化資料，"
            "Rightmove 頁面結構可能已變動，請把實際頁面原始碼貼給我更新解析邏輯。"
        )
        return []

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        print("⚠️ __NEXT_DATA__ 結構化資料解析失敗，Rightmove 頁面結構可能已變動。")
        return []

    try:
        properties = data["props"]["pageProps"]["searchResults"]["properties"]
    except KeyError:
        print(
            "⚠️ __NEXT_DATA__ 裡沒有找到房源列表，"
            "Rightmove 頁面結構可能已變動，請把實際頁面原始碼貼給我更新解析邏輯。"
        )
        return []

    listings = []
    for prop in properties:
        display_prices = prop.get("price", {}).get("displayPrices", [])
        rent_pcm = display_prices[0]["displayPrice"] if display_prices else "未提供租金"
        url = prop.get("propertyUrl", "")
        listings.append({
            "rent_pcm": rent_pcm,
            "address": prop.get("displayAddress", "未提供地址"),
            "bedrooms": prop.get("bedrooms"),
            "property_type": prop.get("propertySubType", ""),
            "url": urljoin(SEARCH_URL, url) if url else "",
        })

    return listings


def filter_listings(listings, address_keywords, bedrooms_filter, property_type_filter):
    address_kws = [kw.lower() for kw in address_keywords if kw]
    type_kws = [t.lower() for t in property_type_filter if t]

    result = []
    for item in listings:
        if address_kws and not any(kw in item["address"].lower() for kw in address_kws):
            continue
        if bedrooms_filter is not None and item["bedrooms"] != bedrooms_filter:
            continue
        if type_kws and item["property_type"].lower() not in type_kws:
            continue
        result.append(item)
    return result


# ==================== 2. Google Sheet 讀寫 ====================
# 欄位順序：Date | Address | Listed rent | Remark | URL
SHEET_HEADER = ["Date", "Address", "Listed rent", "Remark", "URL"]
COL_DATE = 1
COL_RENT = 3
COL_REMARK = 4


def get_sheet():
    if GOOGLE_CREDENTIALS_JSON:
        gc = gspread.service_account_from_dict(json.loads(GOOGLE_CREDENTIALS_JSON))
    else:
        gc = gspread.service_account(filename="credentials.json")
    sh = gc.open(SHEET_NAME)
    worksheet = sh.sheet1
    if not worksheet.get_all_values():
        worksheet.append_row(SHEET_HEADER)
    return worksheet


def load_existing_listings(worksheet):
    records = worksheet.get_all_records()
    existing = {}
    for idx, row in enumerate(records, start=2):  # 第 1 列是標題，資料從第 2 列開始
        url = str(row.get("URL", "")).strip()
        if url:
            existing[url] = {
                "row_num": idx,
                "rent": str(row.get("Listed rent", "")),
            }
    return existing


def process_listings(listings):
    worksheet = get_sheet()
    existing_listings = load_existing_listings(worksheet)
    today_str = datetime.now().strftime("%Y-%m-%d")

    new_items = []
    dropped_items = []

    for item in listings:
        url_key = item["url"].strip()
        if not url_key:
            continue

        if url_key not in existing_listings:
            worksheet.append_row([
                today_str,
                item["address"],
                item["rent_pcm"],
                "NEW (Rightmove)",
                item["url"],
            ])
            new_items.append(item)
        else:
            old_info = existing_listings[url_key]
            if old_info["rent"] != item["rent_pcm"]:
                remark = f"PRICE DROP (was {old_info['rent']})"
                worksheet.update_cell(old_info["row_num"], COL_DATE, today_str)
                worksheet.update_cell(old_info["row_num"], COL_RENT, item["rent_pcm"])
                worksheet.update_cell(old_info["row_num"], COL_REMARK, remark)
                dropped_items.append((item, old_info["rent"]))

    return new_items, dropped_items


# ==================== 3. 主程式 ====================
def main():
    print("開始抓取 Rightmove SE13 5HU 房源...")
    html = fetch_search_page()
    listings = parse_listings(html)
    print(f"共抓到 {len(listings)} 筆房源。")

    if not listings:
        print("未抓到任何房源，請確認 Rightmove 頁面結構是否變動。")
        return

    listings = filter_listings(listings, ADDRESS_FILTERS, BEDROOMS_FILTER, PROPERTY_TYPE_FILTER)
    print(
        f"符合地址關鍵字 {ADDRESS_FILTERS} 其中之一、房間數為 {BEDROOMS_FILTER}、"
        f"房型屬於 {PROPERTY_TYPE_FILTER} 的房源共 {len(listings)} 筆。"
    )

    if not listings:
        print("沒有符合條件的房源，不寫入 Google Sheet。")
        return

    new_items, dropped_items = process_listings(listings)
    print(f"已寫入 Google Sheet：新增 {len(new_items)} 筆，降價 {len(dropped_items)} 筆。")


if __name__ == "__main__":
    main()
