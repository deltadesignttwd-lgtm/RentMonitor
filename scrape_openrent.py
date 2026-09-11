import os
import json
import requests
import gspread
from bs4 import BeautifulSoup
from datetime import datetime
from dotenv import load_dotenv
from urllib.parse import urlencode, urljoin

# ==================== 1. 設定與環境變數 ====================
load_dotenv()

GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
SHEET_NAME = "Rent Monitor"

# 只寫入「地址」包含 ADDRESS_FILTER 且「房型」包含 PROPERTY_TYPE_FILTER 的房源，
# 留空字串則該項不過濾。
ADDRESS_FILTER = "Eastdown Park"
PROPERTY_TYPE_FILTER = "1 Bed Flat"

# OpenRent SE13 5HU (Lewisham) 搜尋結果頁面，1 房、5 分鐘範圍、限已裝潢 (furnishedType=2)
# 用 urlencode 產生查詢字串，確保跟 OpenRent 自己產生的連結编码方式一致
# (空白用 +、逗號用 %2C)，手動拼字串曾因編碼不一致被伺服器回 405。
SEARCH_BASE_URL = "https://www.openrent.co.uk/properties-to-rent/se13-5hu-lewisham-greater-london"
SEARCH_PARAMS = {
    "term": "SE13 5HU Lewisham, Greater London",
    "searchType": "minutes",
    "area": "5",
    "bedrooms_min": "1",
    "bedrooms_max": "1",
    "furnishedType": "2",
}
SEARCH_URL = f"{SEARCH_BASE_URL}?{urlencode(SEARCH_PARAMS)}"

# 加上完整瀏覽器會送的 headers（不只 User-Agent）。
# 405 若是因為 WAF 判斷請求「看起來不像瀏覽器」而擋下，這樣或許能過；
# 但若 openrent.co.uk 是直接擋 GitHub Actions runner 的雲端/機房 IP 段，
# 這裡加 headers 也沒用，那就得從別的網路環境（例如自架 runner）跑。
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
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.openrent.co.uk/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}

# 房源卡片與各欄位的 CSS selector，對照 2026-08 實際頁面原始碼確認過。
# 每張房源卡片是 <a class="pli search-property-card" ...>，不是 div。
LISTING_CARD_SELECTOR = "a.pli"
FIELD_SELECTORS = {
    # 標題格式如「1 Bed Flat, Lee High Road, SE13」，之後會拆成 property_type + address
    "title": ".fs-3",
    # 「£1,595」+「per month」兩個 span，取整個容器文字；
    # 已出租 (Let Agreed) 的房源沒有 .pim，退回抓那個狀態文字本身
    "rent_pcm": ".pim, .fs-4.fw-medium.text-primary",
    # Furnished 狀態一律是該卡片房源特徵列表的最後一個 <li>
    "furnished": "ul.inline-list-divide li:last-child",
}


def _first_text(card, selector_str, default=""):
    for sel in [s.strip() for s in selector_str.split(",")]:
        el = card.select_one(sel)
        if el and el.get_text(strip=True):
            return el.get_text(" ", strip=True)
    return default


def _split_title(title):
    """「1 Bed Flat, Lee High Road, SE13」-> ("1 Bed Flat", "Lee High Road, SE13")"""
    if "," in title:
        property_type, address = title.split(",", 1)
        return property_type.strip(), address.strip()
    return "", title.strip()


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
        title = _first_text(card, FIELD_SELECTORS["title"], "")
        property_type, address = _split_title(title)
        href = card.get("href", "")
        listings.append({
            "rent_pcm": _first_text(card, FIELD_SELECTORS["rent_pcm"], "未提供租金"),
            "address": address or "未提供地址",
            "property_type": property_type or "未提供",
            "furnished": _first_text(card, FIELD_SELECTORS["furnished"], "未提供"),
            "url": urljoin(SEARCH_URL, href) if href else "",
        })

    return listings


def filter_listings(listings, address_keyword, property_type_keyword):
    address_kw = address_keyword.lower()
    type_kw = property_type_keyword.lower()
    return [
        item for item in listings
        if (not address_kw or address_kw in item["address"].lower())
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
                "NEW",
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
    print("開始抓取 OpenRent SE13 5HU 房源...")
    html = fetch_search_page()
    listings = parse_listings(html)
    print(f"共抓到 {len(listings)} 筆房源。")

    if not listings:
        print("未抓到任何房源，請確認 OpenRent 頁面結構是否變動。")
        return

    listings = filter_listings(listings, ADDRESS_FILTER, PROPERTY_TYPE_FILTER)
    print(
        f"符合地址關鍵字 '{ADDRESS_FILTER}' 且房型包含 '{PROPERTY_TYPE_FILTER}' "
        f"的房源共 {len(listings)} 筆。"
    )

    if not listings:
        print("沒有符合條件的房源，不寫入 Google Sheet。")
        return

    new_items, dropped_items = process_listings(listings)
    print(f"已寫入 Google Sheet：新增 {len(new_items)} 筆，降價 {len(dropped_items)} 筆。")


if __name__ == "__main__":
    main()
