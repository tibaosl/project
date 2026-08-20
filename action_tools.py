import asyncio
from playwright.async_api import async_playwright

async def parse_ncu_schedule_table(page):
    print("[Action Agent] 正在解析課表...")

    await page.wait_for_selector("#AutoNumber1", timeout=10000)

    schedule_matrix = await page.evaluate('''() => {
        const table = document.querySelector("#AutoNumber1");
        if (!table) return null;

        const matrix = [];
        const rows = table.querySelectorAll("tr");

        rows.forEach((tr, rowIndex) => {
            if (!matrix[rowIndex]) matrix[rowIndex] = [];
            let colIndex = 0;

            tr.querySelectorAll("td, th").forEach(cell => {
                while (matrix[rowIndex][colIndex] !== undefined) {
                    colIndex++;
                }

                const rowspan = parseInt(cell.getAttribute("rowspan") || "1", 10);
                const colspan = parseInt(cell.getAttribute("colspan") || "1", 10);
                const text = cell.innerText.trim();

                for (let r = 0; r < rowspan; r++) {
                    for (let c = 0; c < colspan; c++) {
                        const targetRow = rowIndex + r;
                        if (!matrix[targetRow]) matrix[targetRow] = [];
                        matrix[targetRow][colIndex + c] = text;
                    }
                }
                colIndex += colspan;
            });
        });

        return matrix;
    }''')

    if not schedule_matrix:
        print("[Action Agent] 找不到表格")
        return None

    if len(schedule_matrix) < 2:
        print("[Action Agent] 表格資料不足，無法解析。")
        return []
    
    headers = schedule_matrix[0] # ['午別', '節次', '時間', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六']
    parsed_courses = []

    for row in schedule_matrix[1:]:
        if not row or len(row) < 3:
            continue
        period_name = str(row[1]).strip() if row[1] else ""
        time_slot = str(row[2]).replace("\n", " ").strip() if row[2] else ""

        if not period_name:
            continue

        for day_idx in range(3, len(headers)):
            if day_idx >= len(row):
                continue
            day_name = str(headers[day_idx]).strip() if headers[day_idx] else f"未知星期({day_idx})"
            cell_content = row[day_idx]

            if cell_content and str(cell_content).strip():
                course_lines = [line.strip() for line in cell_content.split("\n") if line.strip()]
                
                parsed_courses.append({
                    "day": day_name,
                    "period": period_name,
                    "time": time_slot,
                    "raw_content": str(cell_content),
                    "details": course_lines
                })

    print(f"\n[Action Agent] 成功解析課表！共擷取到 {len(parsed_courses)} 個有課的時段區塊：\n")
    print("=================== 個人課表清單 ===================")
    for item in parsed_courses:
        print(f"{item['day']} {item['period']} ({item['time']})】")
        for line in item['details']:
            print(f"   └─ {line}")
        print("-" * 50)

    return parsed_courses

async def main(username, password):
    async with async_playwright() as p:
        browser_ui = await p.chromium.launch(headless=False)
        context_ui = await browser_ui.new_context(locale="zh-TW")
        page_ui = await context_ui.new_page()

        print("[Action Agent] 正在開啟中大 Portal...")
        await page_ui.goto("https://portal.ncu.edu.tw/login")
        await page_ui.get_by_role("textbox", name="帳號").fill(username)
        await page_ui.get_by_role("textbox", name="密碼").fill(password)

        print("\n=======================================================")
        print("[Action Agent 暫停]")
        print("請手動打勾「我不是機器人」並解題。")
        print("完成後請手動點擊「登入 Portal」按鈕，系統將等待 90 秒...")
        print("=======================================================\n")

        try:
            await page_ui.get_by_role("button", name="登入 Portal").wait_for(state="hidden", timeout=90000)
            print("[Action Agent] 登入成功！")
            
            print("[Action Agent] 正在檢查是否有「修改密碼」提示...")
            try:
                cancel_btn = page_ui.get_by_role("button", name="關閉")
                await cancel_btn.wait_for(state="visible", timeout=2000)
                await cancel_btn.click()
                await page_ui.wait_for_timeout(1000) 
            except Exception:
                pass

            print("[Action Agent] 等待 Portal 首頁載入完成，寫入憑證...")
            await page_ui.wait_for_selector("text=學生服務", state="visible", timeout=15000)
            await page_ui.wait_for_load_state("networkidle")

            real_user_agent = await page_ui.evaluate("navigator.userAgent")

            login_state = await context_ui.storage_state()
            await browser_ui.close()
            print("[Action Agent] 畫面關閉，已擷取登入憑證，準備轉入背景執行...")

        except Exception as e:
            await browser_ui.close()
            return {"status": "error", "message": f"登入階段發生錯誤: {e}"}

        print("[Action Agent] 啟動背景隱形爬蟲...")
        browser_bg = await p.chromium.launch(headless=True)

        context_bg = await browser_bg.new_context(
            storage_state=login_state,
            viewport={'width': 1920, 'height': 1080},
            user_agent=real_user_agent,
            locale="zh-TW"
        )
        page_bg = await context_bg.new_page()

        try:
            await page_bg.goto("https://portal.ncu.edu.tw/")
            await page_bg.wait_for_load_state("networkidle")
            
            if "login" in page_bg.url:
                raise Exception("Cookie 傳遞失敗，背景瀏覽器被踢回登入頁面！")
            
            print("[Action Agent] 正在背景導航至課務系統...")
            await page_bg.get_by_text("學生服務", exact=False).first.click()
            await page_bg.wait_for_timeout(200)
            await page_bg.get_by_text("教務相關服務", exact=False).first.click()
            await page_bg.wait_for_timeout(200)

            async with context_bg.expect_page() as new_page_info:
                await page_bg.get_by_text("課務系統", exact=False).first.click()
                
            course_mgr_page = await new_page_info.value
            await course_mgr_page.wait_for_load_state("networkidle")

            print("[Action Agent] 正在解析課表表格...")
            courses = await parse_ncu_schedule_table(course_mgr_page)

            await browser_bg.close()
            return {"status": "success", "data": courses}
            
        except Exception as e:
            print(f"[Action Agent] 任務發生錯誤或超時：{e}")
            await browser_bg.close()
            return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    asyncio.run(main("113502513", "MAX10207max*"))