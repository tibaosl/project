import asyncio
from playwright.async_api import async_playwright

async def ncu_portal_login_tool(username: str, password: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        print("[Action Agent] 正在開啟中大 Portal...")
        await page.goto("https://portal.ncu.edu.tw/login")

        print(f"[Action Agent] 正在填寫帳號與密碼...")
        await page.get_by_role("textbox", name="帳號").press_sequentially(username, delay=50)
        await page.get_by_role("textbox", name="密碼").press_sequentially(password, delay=50)

        print("\n=======================================================")
        print("[Action Agent 暫停]")
        print("請手動打勾「我不是機器人」並解題。")
        print("完成後請手動點擊「登入 Portal」按鈕，系統將等待 90 秒...")
        print("=======================================================\n")

        try:
            await page.get_by_role("button", name="登入 Portal").wait_for(state="hidden", timeout=90000)
            print("[Action Agent] 偵測到登入按鈕消失，判定登入成功。")

        except Exception as e:
            print("[Action Agent] 驗證碼解題超時，任務中斷。")
            await browser.close()
            return {"status": "error", "message": "驗證碼解題超時或登入失敗"}

        await asyncio.sleep(5)
        await browser.close()
        
        return {"status": "success", "message": "成功登入！"}

if __name__ == "__main__":
    asyncio.run(ncu_portal_login_tool("你的帳號", "你的密碼"))