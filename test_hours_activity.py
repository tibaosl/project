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

活動報名測試（預設 dry-run，不會真的送出報名）：
    python test_hours_activity.py --with-login --test-registration=2123

⚠️ 只有「同時」加上 --confirm-registration，才會真的點下報名按鈕送出報名，
   請確定你真的想報名該活動再加這個旗標：
    python test_hours_activity.py --with-login --test-registration=2123 --confirm-registration

取消報名測試，用法對稱，一樣預設 dry-run，要加 --confirm-cancel 才會真的取消：
    python test_hours_activity.py --with-login --test-cancel=2123
    python test_hours_activity.py --with-login --test-cancel=2123 --confirm-cancel

依時數缺口推薦活動（不需登入，公開資料）：
    python test_hours_activity.py --recommend=人文藝術,國際視野,校外服務
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

from activity_tools import (
    search_activities,
    get_activity_detail,
    recommend_activities_for_categories,
    format_activity_summary,
)

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
        print(f"\n查看第一筆活動詳情（id={first_id}）的摘要格式：\n")
        detail = get_activity_detail(first_id)
        print(format_activity_summary(detail))

    # 順便驗證「現場報名」偵測：107影享會(2047)是已知的現場報名案例
    print("\n" + "-" * 70)
    print("驗證現場報名偵測（id=2047，107影享會）：")
    onsite_detail = get_activity_detail("2047")
    for session in onsite_detail.get("sessions", []):
        print(
            f"  {session.get('session_name')}："
            f"registration_mode={session.get('registration_mode')}"
        )


async def test_hours_dashboard(session):
    print("\n" + "=" * 70)
    print("【測試 2】個人時數 dashboard（需要登入）")
    print("=" * 70)

    dashboard_data = await session.get_hours_dashboard()

    print(f"\n目前頁面 URL：{dashboard_data['url']}")
    print(f"是否已達畢業門檻（四大類別都要達標）：{dashboard_data['graduated']}")

    for category, data in dashboard_data["categories"].items():
        status = "✅ 已達標" if data["passed_graduation"] else "⚠️ 尚未達標"
        print(f"\n【{category}】{status}（門檻總計 {data['graduation_required']} 小時）")
        for sub_name, sub in data["subcategories"].items():
            sub_status = "✅" if sub["passed"] else "⚠️"
            print(
                f"    {sub_status} {sub_name}：{sub['confirmed_hours']}/"
                f"{sub['required']} 小時"
                f"（還差 {sub['remaining']}，待核發 {sub['pending_hours']}）"
            )
        print(
            f"    里程碑：畢業門檻 {data['milestones']['畢業門檻']} / "
            f"銀質獎 {data['milestones']['銀質獎']} / "
            f"金質獎 {data['milestones']['金質獎']}"
        )

    print(
        f"\n（原始表格資料仍保留在 raw_tables，共 "
        f"{len(dashboard_data['raw_tables'])} 個，需要時可以對照除錯）"
    )


async def test_activity_registration(session, activity_id: str, confirm: bool):
    print("\n" + "=" * 70)
    title = "【測試 3】活動報名" + ("（confirm=True，會真的送出！）" if confirm else "（dry-run，不會真的送出）")
    print(title)
    print("=" * 70)

    result = await session.register_for_activity_session(activity_id, confirm=confirm)
    print(f"\n結果：{result}")


async def test_activity_cancellation(session, activity_id: str, confirm: bool):
    print("\n" + "=" * 70)
    title = "【測試 4】取消報名" + ("（confirm=True，會真的取消！）" if confirm else "（dry-run，不會真的取消）")
    print(title)
    print("=" * 70)

    result = await session.cancel_activity_registration(activity_id, confirm=confirm)
    print(f"\n結果：{result}")


def test_recommend_activities(subcategory_names: list[str]):
    print("\n" + "=" * 70)
    print(f"【測試 0】依時數缺口推薦活動（不需登入）：{subcategory_names}")
    print("=" * 70)

    # 這裡是離線測試用的假缺口資料（沒有真的登入查 dashboard），
    # remaining/confirmed_hours/required 只是為了讓 reason 文字看起來合理，
    # 不代表真實時數。
    fake_deficiencies = [
        {
            "group": name,
            "subcategory": name,
            "confirmed_hours": 0,
            "required": 0,
            "remaining": 0,
        }
        for name in subcategory_names
    ]

    recommendations = recommend_activities_for_categories(fake_deficiencies)
    for name, items in recommendations.items():
        print(f"\n【{name}】找到 {len(items)} 場：")
        for item in items:
            print(
                f"  - [{item['activity_id']}] {item['activity_title']} / "
                f"{item['session_name']} | {item['tag']} | "
                f"報名期間：{item['signup_period']}"
            )


async def run_login_tests():
    username = os.environ.get("NCU_USERNAME", "")
    password = os.environ.get("NCU_PASSWORD", "")

    if not username or not password:
        print(
            "沒有偵測到 NCU_USERNAME / NCU_PASSWORD 環境變數，跳過需要登入的測試。\n"
            "請參考本檔案最上方的說明設定環境變數後再加 --with-login 執行。"
        )
        return

    from action_tools import NCUSession

    # 兩個測試共用同一個 NCUSession（只登入一次），
    # 也共用同一個 event loop（只呼叫一次 asyncio.run）——
    # 在某些 Windows 環境下，短時間內重複建立/銷毀 ProactorEventLoop
    # 會被系統擋下 socket 存取（WinError 10013），跟防火牆/防毒攔截新
    # 程序的網路存取是類似的狀況，所以避免重複登入、重複開新 event loop。
    async with NCUSession(username, password) as session:
        await test_hours_dashboard(session)

        registration_arg = next(
            (a for a in sys.argv if a.startswith("--test-registration=")), None
        )
        if registration_arg:
            target_activity_id = registration_arg.split("=", 1)[1]
            confirm_registration = "--confirm-registration" in sys.argv
            await test_activity_registration(
                session, target_activity_id, confirm_registration
            )

        cancel_arg = next(
            (a for a in sys.argv if a.startswith("--test-cancel=")), None
        )
        if cancel_arg:
            target_activity_id = cancel_arg.split("=", 1)[1]
            confirm_cancel = "--confirm-cancel" in sys.argv
            await test_activity_cancellation(
                session, target_activity_id, confirm_cancel
            )


if __name__ == "__main__":
    recommend_arg = next(
        (a for a in sys.argv if a.startswith("--recommend=")), None
    )
    if recommend_arg:
        names = recommend_arg.split("=", 1)[1].split(",")
        test_recommend_activities(names)
    else:
        test_activity_query()

    if "--with-login" not in sys.argv:
        print(
            "\n(略過時數 dashboard / 活動報名 / 取消報名測試；要測試請加上 "
            "--with-login 並設定 NCU_USERNAME / NCU_PASSWORD 環境變數)"
        )
    else:
        asyncio.run(run_login_tests())
