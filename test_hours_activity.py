"""測試「活動查詢」與「時數 dashboard」相關功能。

活動查詢是公開的，不需要帳密，可以直接跑：
    python test_hours_activity.py

時數 dashboard 需要登入，帳密請用下面任一種方式提供（不要寫死在程式碼裡）：

1. 在專案根目錄的 .env 裡加兩行（.env 已經在 .gitignore，不會被 push 上去）：
    NCU_USERNAME=你的學號
    NCU_PASSWORD=你的密碼
   然後執行：
    python test_hours_activity.py --with-login

2. 或是直接用環境變數（不寫進任何檔案）：
    NCU_USERNAME=你的學號 NCU_PASSWORD=你的密碼 python test_hours_activity.py --with-login

   （PowerShell 版本）
    $env:NCU_USERNAME="你的學號"; $env:NCU_PASSWORD="你的密碼"; python test_hours_activity.py --with-login
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

from activity_tools import search_activities, get_activity_detail

load_dotenv()


def test_activity_query():
    print("\n" + "=" * 70)
    print("【測試 1】公開活動查詢（不需登入）")
    print("=" * 70)

    results = search_activities()
    print(f"共找到 {len(results)} 筆活動\n")

    for item in results[:5]:
        print(
            f"- [{item['activity_id']}] {item['title']} "
            f"({item['status']}) 對象：{item['target_audience']}"
        )

    if results:
        first_id = results[0]["activity_id"]
        print(f"\n查看第一筆活動詳情（id={first_id}）：")
        detail = get_activity_detail(first_id)
        print(f"活動名稱：{detail.get('title')}")
        print(f"承辦單位：{detail.get('department')}")
        for session in detail.get("sessions", []):
            print(
                f"  場次「{session.get('session_name')}」："
                f"{session.get('event_period')} | "
                f"軟實力時數：{session.get('soft_skill_hours_tag')} | "
                f"需登入報名：{session.get('requires_login_to_register')}"
            )


async def test_hours_dashboard():
    print("\n" + "=" * 70)
    print("【測試 2】個人時數 dashboard（需要登入）")
    print("=" * 70)

    username = os.environ.get("NCU_USERNAME", "")
    password = os.environ.get("NCU_PASSWORD", "")

    if not username or not password:
        print(
            "沒有偵測到 NCU_USERNAME / NCU_PASSWORD 環境變數，跳過這項測試。\n"
            "請參考本檔案最上方的說明設定環境變數後再加 --with-login 執行。"
        )
        return

    from action_tools import NCUSession

    async with NCUSession(username, password) as session:
        dashboard_data = await session.get_hours_dashboard()

        print(f"\n目前頁面 URL：{dashboard_data['url']}")

        print(f"\n共擷取到 {len(dashboard_data['raw_tables'])} 個表格：")
        for i, table_text in enumerate(dashboard_data["raw_tables"], 1):
            print(f"\n--- 表格 {i} ---")
            print(table_text[:1000])

        print(
            f"\n共擷取到 {len(dashboard_data['raw_summary_blocks'])} 個統計/卡片區塊："
        )
        for i, block_text in enumerate(dashboard_data["raw_summary_blocks"][:10], 1):
            print(f"\n--- 區塊 {i} ---")
            print(block_text[:300])

        print(
            "\n[提示] 把上面印出來的內容回報給我，"
            "我就能把 get_hours_dashboard() 改成正式的欄位解析。"
        )


if __name__ == "__main__":
    test_activity_query()

    if "--with-login" in sys.argv:
        asyncio.run(test_hours_dashboard())
    else:
        print(
            "\n(略過時數 dashboard 測試；要測試請加上 --with-login 並設定 "
            "NCU_USERNAME / NCU_PASSWORD 環境變數)"
        )
