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
# 目前 Eastdown Park 可能沒有現貨可測試，第一次先設 "" 確認真的有抓到房源，
# 確認 OK 後再改回 "Eastdown Park" 當正式篩選條件。
ADDRESS_FILTER = ""

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

# ⚠️ 未對照真實頁面原始碼確認過！這個 sandbox 連不到 zoopla.co.uk（跟一開始連不到
# openrent.co.uk 一樣），所以下面的 selector 是根據 Zoopla 常見的 data-testid 命名
# 猜的起始版本，每個欄位都放了好幾個候選 selector（用逗號分隔，依序嘗試）。
# 如果實跑抓到 0 筆，或欄位是空的/錯的：
#   1. 在瀏覽器打開 SEARCH_URL，右鍵一張房源卡片 -> 檢查(Inspect)
#   2. 把那張卡片的 HTML 複製貼給我，我會照實際結構修正這裡的 selector
#      （跟當初修 scrape_openrent.py 的 LISTING_CARD_SELECTOR 一樣的流程）
LISTING_CARD_SELECTOR = (
    "[data-testid='search-result'], [data-testid='regular-listing'], "
    "div[data-testid*='listing'], li[data-testid*='listing']"
)
FIELD_SELECTORS = {
    "address": "address, [data-testid='listing-title'], h2",
    "rent_pcm": "[data-testid='listing-price'], p[data-testid*='price']",
    "property_type": "[data-testid='listing-spec'], p[data-testid*='spec']",
    # Zoopla 搜尋結果卡片通常不會直接顯示 Furnished/Unfurnished（要進詳情頁才有），
    # 這裡只是盡量在卡片裡找含 "urnished" 字樣的標籤，抓不到就顯示「未提供」。
    "furnished": ":-soup-contains('urnished')",
}


def _innermost(elements):
    """篩掉「包住其他 match」的外層元素，只留下最具體的那個。
    用來修正 :-soup-contains 之類的 selector：外層容器的文字通常也包含子元素
    的文字，所以 select() 會連同外層一起抓到，這裡把它濾掉只留最裡層的那個。"""
    return [
        el for el in elements
        if not any(other is not el and other in el.descendants for other in elements)
    ]


def _first_text(card, selector_str, default=""):
    for sel in [s.strip() for s in selector_str.split(",")]:
        matches = _innermost(card.select(sel))
        if matches and matches[0].get_text(strip=True):
            return matches[0].get_text(" ", strip=True)
    return default


def _select_cards(soup, selector_str):
    for sel in [s.strip() for s in selector_str.split(",")]:
        cards = soup.select(sel)
        if cards:
            return cards
    return []


def fetch_search_page():
    resp = requests.get(SEARCH_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def parse_listings(html):
    soup = BeautifulSoup(html, "html.parser")
    cards = _select_cards(soup, LISTING_CARD_SELECTOR)

    if not cards:
        print(
            f"⚠️ 找不到任何符合 '{LISTING_CARD_SELECTOR}' 的房源卡片，"
            f"Zoopla 頁面結構可能跟猜測的不一樣，請檢查並更新 "
            f"LISTING_CARD_SELECTOR / FIELD_SELECTORS。"
        )
        return []

    listings = []
    for card in cards:
        listings.append({
            "rent_pcm": _first_text(card, FIELD_SELECTORS["rent_pcm"], "未提供租金"),
            "address": _first_text(card, FIELD_SELECTORS["address"], "未提供地址"),
            "property_type": _first_text(card, FIELD_SELECTORS["property_type"], "未提供"),
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
