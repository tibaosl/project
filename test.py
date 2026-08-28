import asyncio
from action_tools import *


async def main():
    username = "113502564"
    password = "0928027067aB"
    keyword = "日文"

    async with NCUSession(username, password) as session:
        # ==================================================
        # 1. 取得個人課表並計算已佔用時段
        # ==================================================
        print("\n>>> [步驟 1] 正在讀取目前個人課表...")
        schedule_data = await get_schedule(session)
        
        if not schedule_data:
            print("無法取得課表資料或目前無選課記錄。")
            schedule_data = []

        occupied_slots = build_schedule_occupied_slots(schedule_data)
        print(f"✓ 課表分析完成，目前已選 {len(occupied_slots)} 節課：")
        print(f"  佔用時段: {sorted(list(occupied_slots))}\n")

        # ==================================================
        # 2. 搜尋目標關鍵字課程
        # ==================================================
        print(f">>> [步驟 2] 正在搜尋「{keyword}」相關課程...")
        courses = await search_courses(session, keyword)
        print(f"✓ 搜尋完成，共找到 {len(courses)} 門課程。\n")

        # ==================================================
        # 3. 衝堂過濾與結果輸出
        # ==================================================
        print(">>> [步驟 3] 正在進行衝堂比對...")
        available, conflicts = filter_available_courses(courses, occupied_slots)

        print("\n" + "=" * 70)
        print(f"【可選課程清單（無衝堂）】 共 {len(available)} 門")
        print("=" * 70)
        for c in available:
            print(
                f"✓ 流水號: {c.get('serial', ''):<6} | "
                f"課號: {c.get('course_no', ''):<8} | "
                f"課名: {c.get('title', ''):<14} | "
                f"教師: {c.get('teacher', ''):<8} | "
                f"時段: {c.get('time', ''):<6} | "
                f"學分: {c.get('credits', '')}"
            )

        print("\n" + "=" * 70)
        print(f"【衝堂課程清單（已排除）】 共 {len(conflicts)} 門")
        print("=" * 70)
        for c in conflicts:
            print(
                f"✗ 流水號: {c.get('serial', ''):<6} | "
                f"課號: {c.get('course_no', ''):<8} | "
                f"課名: {c.get('title', ''):<14} | "
                f"時段: {c.get('time', ''):<6} | "
                f"原因: {c.get('conflict_reason', '')}"
            )
        print("=" * 70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())