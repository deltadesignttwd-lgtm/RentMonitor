import os
import json
import gspread
from dotenv import load_dotenv

load_dotenv()

def test_google_sheet_connection():
    try:
        # 1. 載入憑證檔並建立連線
        creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if creds_json:
            gc = gspread.service_account_from_dict(json.loads(creds_json))
        else:
            gc = gspread.service_account(filename="credentials.json")

        # 2. 打開你的 Google Sheet
        sheet_name = "Rent Monitor"
        sh = gc.open(sheet_name)
        worksheet = sh.sheet1  # 選擇第一個工作表

        print(f"✅ 成功連線到 Google Sheet: '{sheet_name}'！")

        # 3. 測試寫入一筆測試資料
        test_row = [
            "2026-08-16",
            "Lee High Road, SE13",
            "£1,595",
            "TEST",
            "https://www.openrent.co.uk/property-to-rent/london/1-bed-flat-lee-high-road-se13/2885752"
        ]

        worksheet.append_row(test_row)
        print("🎉 成功將測試房源資料寫入 Google Sheet 列尾！")

    except gspread.exceptions.SpreadsheetNotFound:
        print(f"❌ 找不到名稱為 '{sheet_name}' 的 Google Sheet。請檢查名稱是否完全一致，且已開啟共用權限給 Service Account。")
    except Exception as e:
        print(f"❌ 連線失敗，錯誤訊息：{e}")

if __name__ == "__main__":
    test_google_sheet_connection()
