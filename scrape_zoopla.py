import os
import re
import json
import requests
import gspread
from datetime import datetime
from dotenv import load_dotenv

# ==================== 1. 設定與環境變數 ====================
load_dotenv()

GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
SHEET_NAME = "Rent Monitor"

# 只寫入「地址」符合 ADDRESS_FILTERS 其中一個關鍵字、且「房型描述」包含
# PROPERTY_TYPE_FILTER 的房源。留空則該項不過濾。
ADDRESS_FILTERS = ["Eastdown Park", "Dermody Road", "Wisteria Road", "Gilmore Road", "Lee High Road"]
PROPERTY_TYPE_FILTER = "1 bed flat"

# Zoopla SE13 5HU (Eastdown Park) 搜尋結果頁面，已在 Zoopla 網站上套用 1 房篩選
SEARCH_URL = (
    "https://www.zoopla.co.uk/to-rent/property/1-bedroom/london/eastdown-park/se13-5hu/"
    "?include_rented=true&is_retirement_home=false&is_shared_accommodation=false"
    "&is_student_accommodation=false&q=se13%205hu&search_source=to-rent"
)

# 跟 scrape_openrent.py 用同一組完整瀏覽器 headers。
# 注意：Accept-Encoding 不能列 "br" -- requests 沒裝 brotli 套件的話沒辦法
# 自動解壓縮 Brotli 壓縮的內容，Zoopla 的伺服器會用 br 回應，導致 resp.text
# 變成一堆亂碼、完全比對不到任何 selector（OpenRent 剛好沒用 br 才沒事）。
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
    "Referer": "https://www.zoopla.co.uk/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}

# Zoopla 卡片可見的 HTML 沒有獨立的「房型」欄位（只有 bed/bath/reception 數量），
# 但頁面另外內嵌了一段 schema.org 結構化資料 <script id="lsrp-schema"
# type="application/ld+json">，裡面每筆房源的 "name" 欄位是完整描述，例如
# 「1 bed flat to rent near Eastdown Park, London SE13」，可以直接拿來篩選房型，
# 也比對照 CSS class 穩定（不會因為改版換掉 class hash 而失效）。
LSRP_SCHEMA_PATTERN = re.compile(
    r'<script id="lsrp-schema"[^>]*>(.*?)</script>', re.DOTALL
)


def fetch_search_page():
    resp = requests.get(SEARCH_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def parse_listings(html):
    match = LSRP_SCHEMA_PATTERN.search(html)
    if not match:
        print(
            "⚠️ 找不到 lsrp-schema 結構化資料，"
            "Zoopla 頁面結構可能已變動，請把實際頁面原始碼貼給我更新解析邏輯。"
        )
        return []

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        print("⚠️ lsrp-schema 結構化資料解析失敗，Zoopla 頁面結構可能已變動。")
        return []

    listings = []
    for graph_item in data.get("@graph", []):
        if graph_item.get("@type") != "SearchResultsPage":
            continue
        item_list = graph_item.get("mainEntity", {}).get("itemListElement", [])
        for entry in item_list:
            item = entry.get("item", {})
            offers = item.get("offers", {})
            related = item.get("isRelatedTo", {})
            price = offers.get("price", "")
            listings.append({
                "rent_pcm": f"£{price} pcm" if price else "未提供租金",
                "address": related.get("address", "未提供地址"),
                "property_type": item.get("name", ""),
                "url": item.get("url", ""),
            })

    if not listings:
        print(
            "⚠️ lsrp-schema 裡沒有找到任何房源項目，"
            "Zoopla 頁面結構可能已變動，請把實際頁面原始碼貼給我更新解析邏輯。"
        )

    return listings


def filter_listings(listings, address_keywords, property_type_keyword):
    address_kws = [kw.lower() for kw in address_keywords if kw]
    type_kw = property_type_keyword.lower()
    return [
        item for item in listings
        if (not address_kws or any(kw in item["address"].lower() for kw in address_kws))
        and (not type_kw or type_kw in item["property_type"].lower())
    ]


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
                "NEW (Zoopla)",
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
    print("開始抓取 Zoopla SE13 5HU 房源...")
    html = fetch_search_page()
    listings = parse_listings(html)
    print(f"共抓到 {len(listings)} 筆房源。")

    if not listings:
        debug_file = "zoopla_debug.html"
        with open(debug_file, "w", encoding="utf-8") as f:
            f.write(html)
        print(
            f"未抓到任何房源。已將程式實際收到的網頁內容存成 {debug_file}，"
            f"請用瀏覽器或記事本打開它，確認裡面是不是正常的 Zoopla 房源列表頁"
            f"（還是被導向了驗證頁/空白頁/不同內容），並回報結果。"
        )
        return

    listings = filter_listings(listings, ADDRESS_FILTERS, PROPERTY_TYPE_FILTER)
    print(
        f"符合地址關鍵字 {ADDRESS_FILTERS} 其中之一，且房型包含 "
        f"'{PROPERTY_TYPE_FILTER}' 的房源共 {len(listings)} 筆。"
    )

    if not listings:
        print("沒有符合條件的房源，不寫入 Google Sheet。")
        return

    new_items, dropped_items = process_listings(listings)
    print(f"已寫入 Google Sheet：新增 {len(new_items)} 筆，降價 {len(dropped_items)} 筆。")


if __name__ == "__main__":
    main()
