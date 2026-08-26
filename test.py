import asyncio
from action_tools import NCUSession, search_courses

async def main():
    username = "帳號"
    password = "密碼"

    async with NCUSession(username, password) as session:
        print("\n========== 開始測試選課系統 ==========\n")

        results = await search_courses(
            session,
            "日文",
        )

        print("\n========== 測試結果 ==========")

        if results:
            print(f"成功搜尋到 {len(results)} 門課程")

            for course in results:
                print(
                    f"{course['course_no']} "
                    f"{course['title']} "
                    f"{course['teacher']}"
                )
        else:
            print("沒有搜尋到課程")


if __name__ == "__main__":
    asyncio.run(main())