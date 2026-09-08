import asyncio
from action_tools import (
    NCUSession,
    build_schedule_occupied_slots,
    get_schedule,
    init_db,
    query_courses_by_keywords,
    save_courses_to_db,
    save_student_slots,
    tool_check_course_syllabus,
    tool_search_available_courses,
)


async def main():
    init_db()

    username = "113502564"
    password = "0928027067aB"

    # ==========================================
    # 步驟 1：爬取學生課表與目標課程資料庫
    # ==========================================
    async with NCUSession(username, password) as session:
        print("[初始化] 正在讀取學生課表並存入快取...")
        schedule_data = await get_schedule(session)
        occupied_slots = build_schedule_occupied_slots(schedule_data or [])
        save_student_slots(occupied_slots)
        print(f"✓ 學生已選時段: {sorted(list(occupied_slots))}")

        print("[初始化] 正在抓取「日文」公開課程資料 (含課綱)...")
        page = await session.context.new_page()
        courses = await query_courses_by_keywords(page, "日文", fetch_outlines=True)
        save_courses_to_db(courses)
        await page.close()
        print(f"✓ 已存入 {len(courses)} 門課程至 SQLite\n")

    # ==========================================
    # 步驟 2：模擬 Agent 調用 Tools 回答使用者問題
    # ==========================================
    print("=" * 60)
    print("【模擬 Agent 執行 Tool 1: 搜尋非衝堂課程】")
    res1 = tool_search_available_courses(keyword="日文", max_credits=3)
    print(res1)

    print("\n" + "=" * 60)
    print("【模擬 Agent 執行 Tool 2: 調閱特定課綱】")
    res2 = tool_check_course_syllabus("日文(一)A")
    print(res2)


if __name__ == "__main__":
    asyncio.run(main())