import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from dotenv import load_dotenv

# ==================== 1. 設定與環境變數 ====================
load_dotenv()

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

# Zoopla SE13 5HU (Eastdown Park) 搜尋結果頁面，1 房
SEARCH_URL = (
    "https://www.zoopla.co.uk/to-rent/property/1-bedroom/london/eastdown-park/se13-5hu/"
    "?include_rented=true&is_retirement_home=false&is_shared_accommodation=false"
    "&is_student_accommodation=false&q=se135hu&search_source=to-rent"
)

# 只保留「地址」包含這個字串的房源（不分大小寫）；設成空字串 "" 代表不篩選，全部列出。
ADDRESS_FILTER = "Eastdown Park"

# 跟 scrape_openrent.py 用同一組完整瀏覽器 headers。
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
    "Referer": "https://www.zoopla.co.uk/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}

# 房源卡片與各欄位的 CSS selector，對照 2026-08 實際頁面原始碼確認過。
# 每張房源卡片是 <a data-testid="listing-card-content">，包住價格/坪數/地址/簡介。
# class 名稱是 CSS Modules 產生的 hash（例如 price_priceText__TArfK），後面那段
# hash 可能隨改版變動，所以用 [class*='...'] 只比對前面穩定的部分。
LISTING_CARD_SELECTOR = "a[data-testid='listing-card-content']"
FIELD_SELECTORS = {
    "address": "address",
    "rent_pcm": "[class*='price_priceText']",
    # Zoopla 卡片沒有獨立的房型欄位，這裡用「1 bed / 1 bath / 1 reception」那行代替。
    "property_type": "[class*='amenities_amenityListSlim']",
}

# Zoopla 搜尋結果頁面（不管是看得到的 HTML 還是內嵌的 JSON 資料）完全沒有
# Furnished/Unfurnished 欄位——要點進每筆房源的詳情頁才有，這裡固定顯示這段文字，
# 不用猜測性的 selector 去硬抓（避免誤抓到簡介文字裡剛好出現的 "unfurnished" 字樣）。
FURNISHED_NOT_AVAILABLE = "未提供 (Zoopla 搜尋結果頁未顯示，需進入房源詳情頁)"


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
        listings.append({
            "rent_pcm": _first_text(card, FIELD_SELECTORS["rent_pcm"], "未提供租金"),
            "address": _first_text(card, FIELD_SELECTORS["address"], "未提供地址"),
            "property_type": _first_text(card, FIELD_SELECTORS["property_type"], "未提供"),
            "furnished": FURNISHED_NOT_AVAILABLE,
        })

    return listings


def filter_by_address(listings, keyword):
    if not keyword:
        return listings
    keyword_lower = keyword.lower()
    return [item for item in listings if keyword_lower in item["address"].lower()]


# ==================== 2. Telegram 發送 ====================
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    res = requests.post(url, json=payload)
    return res.status_code == 200


# ==================== 3. 組合報告 ====================
def build_report(listings):
    lines = [
        "🏠 *SE13 5HU 租屋監控 (Zoopla)*",
        f"本次共掃描到 {len(listings)} 筆房源。",
        "",
    ]

    for item in listings:
        lines.append(
            f"📍 *地址*: {item['address']}\n"
            f"💰 *租金*: {item['rent_pcm']}\n"
            f"🛏️ *房型*: {item['property_type']}\n"
            f"🛋️ *傢俱*: {item['furnished']}"
        )
        lines.append("")

    lines.append(f"📅 更新時間: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    return "\n".join(lines)


# ==================== 4. 主程式 ====================
def main():
    print("🤖 開始抓取 Zoopla SE13 5HU 房源...")
    html = fetch_search_page()
    listings = parse_listings(html)
    print(f"共抓到 {len(listings)} 筆房源。")

    if not listings:
        send_telegram("⚠️ SE13 5HU 租屋監控 (Zoopla)：本次未抓到任何房源，請確認 Zoopla 頁面結構是否變動。")
        return

    filtered = filter_by_address(listings, ADDRESS_FILTER)
    if ADDRESS_FILTER:
        print(f"套用地址篩選 '{ADDRESS_FILTER}' 後剩 {len(filtered)} 筆。")
    listings = filtered

    if not listings:
        if ADDRESS_FILTER:
            send_telegram(f"ℹ️ SE13 5HU 租屋監控 (Zoopla)：本次沒有地址包含「{ADDRESS_FILTER}」的房源。")
        else:
            send_telegram("⚠️ SE13 5HU 租屋監控 (Zoopla)：本次未抓到任何房源，請確認 Zoopla 頁面結構是否變動。")
        return

    report = build_report(listings)

    if send_telegram(report):
        print("🎉 Telegram 通報發送成功！")
    else:
        print("❌ Telegram 發送失敗。")


if __name__ == "__main__":
    main()
