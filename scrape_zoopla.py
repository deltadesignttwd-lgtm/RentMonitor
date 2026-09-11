import os
import json
import requests
import gspread
from bs4 import BeautifulSoup
from datetime import datetime
from dotenv import load_dotenv
from urllib.parse import urljoin

# ==================== 1. 設定與環境變數 ====================
load_dotenv()

GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
SHEET_NAME = "Rent Monitor"

# 只寫入「地址」符合 ADDRESS_FILTERS 其中一個關鍵字的房源。
# Zoopla 卡片沒有獨立的房型欄位（見下方 FIELD_SELECTORS 註解），所以這裡
# 不像 scrape_openrent.py 一樣加房型篩選，只篩地址。
ADDRESS_FILTERS = ["Eastdown Park", "Dermody Road", "Wisteria Road", "Gilmore Road", "Lee High Road"]

# Zoopla SE13 5HU (Eastdown Park) 搜尋結果頁面
SEARCH_URL = (
    "https://www.zoopla.co.uk/to-rent/property/london/eastdown-park/se13-5hu/"
    "?is_retirement_home=false&is_shared_accommodation=false"
    "&is_student_accommodation=false&q=se13+5hu&search_source=home&recent_search=true"
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

# 房源卡片與各欄位的 CSS selector，對照先前實際頁面原始碼確認過。
# 每張房源卡片是 <a data-testid="listing-card-content">，包住價格/坪數/地址/簡介。
# class 名稱是 CSS Modules 產生的 hash（例如 price_priceText__TArfK），後面那段
# hash 可能隨改版變動，所以用 [class*='...'] 只比對前面穩定的部分。
# 注意：這個 URL 換過（拿掉了路徑裡的 "1-bedroom"），Zoopla 頁面結構也可能已經
# 改變，如果抓不到卡片，把實際頁面原始碼貼給我，我再更新這裡的 selector。
LISTING_CARD_SELECTOR = "a[data-testid='listing-card-content']"
FIELD_SELECTORS = {
    "address": "address",
    "rent_pcm": "[class*='price_priceText']",
}


def _first_text(card, selector_str, default=""):
    el = card.select_one(selector_str)
    if el and el.get_text(strip=True):
        return el.get_text(" ", strip=True)
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
            f"Zoopla 頁面結構可能已變動，請檢查並更新 "
            f"LISTING_CARD_SELECTOR / FIELD_SELECTORS。"
        )
        return []

    listings = []
    for card in cards:
        href = card.get("href", "")
        listings.append({
            "rent_pcm": _first_text(card, FIELD_SELECTORS["rent_pcm"], "未提供租金"),
            "address": _first_text(card, FIELD_SELECTORS["address"], "未提供地址"),
            "url": urljoin(SEARCH_URL, href) if href else "",
        })

    return listings


def filter_by_address(listings, keywords):
    keywords_lower = [kw.lower() for kw in keywords if kw]
    if not keywords_lower:
        return listings
    return [
        item for item in listings
        if any(kw in item["address"].lower() for kw in keywords_lower)
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

    listings = filter_by_address(listings, ADDRESS_FILTERS)
    print(f"符合地址關鍵字 {ADDRESS_FILTERS} 其中之一的房源共 {len(listings)} 筆。")

    if not listings:
        print("沒有符合條件的房源，不寫入 Google Sheet。")
        return

    new_items, dropped_items = process_listings(listings)
    print(f"已寫入 Google Sheet：新增 {len(new_items)} 筆，降價 {len(dropped_items)} 筆。")


if __name__ == "__main__":
    main()
