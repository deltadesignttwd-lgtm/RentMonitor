import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from dotenv import load_dotenv
from urllib.parse import urlencode

# ==================== 1. 設定與環境變數 ====================
load_dotenv()

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

# OpenRent SE13 5HU (Lewisham) 搜尋結果頁面，1 房、10 分鐘範圍
# 用 urlencode 產生查詢字串，確保跟 OpenRent 自己產生的連結编码方式一致
# (空白用 +、逗號用 %2C)，手動拼字串曾因編碼不一致被伺服器回 405。
SEARCH_BASE_URL = "https://www.openrent.co.uk/properties-to-rent/se13-5hu-lewisham-greater-london"
SEARCH_PARAMS = {
    "term": "SE13 5HU Lewisham, Greater London",
    "searchType": "minutes",
    "area": "10",
    "bedrooms_min": "1",
    "bedrooms_max": "1",
}
SEARCH_URL = f"{SEARCH_BASE_URL}?{urlencode(SEARCH_PARAMS)}"

# 只保留「地址」包含這個字串的房源（不分大小寫）；設成空字串 "" 代表不篩選，全部列出。
# 目前設成 "Lee High Road" 是測試用（因為現在 Eastdown Park 沒有房源可以驗證有抓到）；
# 測試 OK 後把它換成 "Eastdown Park" 就是正式篩選條件。
ADDRESS_FILTER = "Lee High Road"

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
        listings.append({
            "rent_pcm": _first_text(card, FIELD_SELECTORS["rent_pcm"], "未提供租金"),
            "address": address or "未提供地址",
            "property_type": property_type or "未提供",
            "furnished": _first_text(card, FIELD_SELECTORS["furnished"], "未提供"),
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
        "🏠 *SE13 5HU 租屋監控*",
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
    print("🤖 開始抓取 OpenRent SE13 5HU 房源...")
    html = fetch_search_page()
    listings = parse_listings(html)
    print(f"共抓到 {len(listings)} 筆房源。")

    if not listings:
        send_telegram("⚠️ SE13 5HU 租屋監控：本次未抓到任何房源，請確認 OpenRent 頁面結構是否變動。")
        return

    filtered = filter_by_address(listings, ADDRESS_FILTER)
    if ADDRESS_FILTER:
        print(f"套用地址篩選 '{ADDRESS_FILTER}' 後剩 {len(filtered)} 筆。")
    listings = filtered

    if not listings:
        if ADDRESS_FILTER:
            send_telegram(f"ℹ️ SE13 5HU 租屋監控：本次沒有地址包含「{ADDRESS_FILTER}」的房源。")
        else:
            send_telegram("⚠️ SE13 5HU 租屋監控：本次未抓到任何房源，請確認 OpenRent 頁面結構是否變動。")
        return

    report = build_report(listings)

    if send_telegram(report):
        print("🎉 Telegram 通報發送成功！")
    else:
        print("❌ Telegram 發送失敗。")


if __name__ == "__main__":
    main()
