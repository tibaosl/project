import asyncio
from typing import Any, Optional
import re
import re
from typing import Any
from playwright.async_api import Page

from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

# Portal
PORTAL_LOGIN_URL = "https://portal.ncu.edu.tw/login"
PORTAL_HOME_URL = "https://portal.ncu.edu.tw/"

# 選課系統
REGISTRATION_LOGIN_URL = "https://cis.ncu.edu.tw/Course/main/login"
REGISTRATION_HOME_URL = "https://cis.ncu.edu.tw/Course/main/sign/selectCourse?step=3"

async def parse_ncu_schedule_table(
    page: Page,
) -> Optional[list[dict[str, Any]]]:
    """解析中大課務系統的課表表格。

    這個 function 只負責「解析」，不負責登入或開啟 Portal。
    """

    print("[Action Agent] 正在解析課表...")

    await page.wait_for_selector("#AutoNumber1", timeout=10000)

    schedule_matrix = await page.evaluate(
        """() => {
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

                    const rowspan = parseInt(
                        cell.getAttribute("rowspan") || "1",
                        10
                    );

                    const colspan = parseInt(
                        cell.getAttribute("colspan") || "1",
                        10
                    );

                    const text = cell.innerText.trim();

                    for (let r = 0; r < rowspan; r++) {
                        for (let c = 0; c < colspan; c++) {
                            const targetRow = rowIndex + r;

                            if (!matrix[targetRow]) {
                                matrix[targetRow] = [];
                            }

                            matrix[targetRow][colIndex + c] = text;
                        }
                    }

                    colIndex += colspan;
                });
            });

            return matrix;
        }"""
    )

    if not schedule_matrix:
        print("[Action Agent] 找不到表格")
        return None

    if len(schedule_matrix) < 2:
        print("[Action Agent] 表格資料不足，無法解析。")
        return []

    headers = schedule_matrix[0]
    parsed_courses = []

    for row in schedule_matrix[1:]:
        if not row or len(row) < 3:
            continue

        period_name = str(row[1]).strip() if row[1] else ""

        time_slot = (
            str(row[2]).replace("\n", " ").strip()
            if row[2]
            else ""
        )

        if not period_name:
            continue

        for day_idx in range(3, len(headers)):
            if day_idx >= len(row):
                continue

            day_name = (
                str(headers[day_idx]).strip()
                if headers[day_idx]
                else f"未知星期({day_idx})"
            )

            cell_content = row[day_idx]

            if cell_content and str(cell_content).strip():
                course_lines = [
                    line.strip()
                    for line in cell_content.split("\n")
                    if line.strip()
                ]

                parsed_courses.append(
                    {
                        "day": day_name,
                        "period": period_name,
                        "time": time_slot,
                        "raw_content": str(cell_content),
                        "details": course_lines,
                    }
                )

    print(
        f"\n[Action Agent] 成功解析課表！"
        f"共擷取到 {len(parsed_courses)} 個有課的時段區塊：\n"
    )

    print("=================== 個人課表清單 ===================")

    for item in parsed_courses:
        print(
            f"{item['day']} {item['period']} "
            f"({item['time']})"
        )

        for line in item["details"]:
            print(f"   └─ {line}")

        print("-" * 50)

    return parsed_courses


class NCUSession:
    """管理一次 NCU session。

    Portal 與選課系統是兩個獨立網站，因此分別登入：

    1. Portal
       https://portal.ncu.edu.tw/login

    2. 選課系統
       https://cis.ncu.edu.tw/Course/main/login

    Portal page、課務系統 page、選課系統 page
    都使用同一個 BrowserContext，但登入狀態彼此獨立。
    """

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password

        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None

        # Portal
        self.page: Optional[Page] = None

        # 課務系統
        self.course_mgr_page: Optional[Page] = None

        # 選課系統
        self.registration_page: Optional[Page] = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def start(self):
        """登入 Portal 並建立背景 BrowserContext。"""

        self.playwright = await async_playwright().start()
        login_state, user_agent = await self._login_interactively()

        print("[Action Agent] 啟動背景隱形爬蟲...")

        # debug 完記得改回 headless=True
        self.browser = await self.playwright.chromium.launch(
            headless=False
        )

        self.context = await self.browser.new_context(
            storage_state=login_state,
            viewport={"width": 1920, "height": 1080},
            user_agent=user_agent,
            locale="zh-TW",
        )

        # ====================================================
        # Portal page
        # ====================================================

        self.page = await self.context.new_page()

        await self.page.goto(PORTAL_HOME_URL)

        await self.page.wait_for_load_state("networkidle")

        if "login" in self.page.url:
            raise RuntimeError(
                "Cookie 傳遞失敗，背景瀏覽器被踢回 Portal 登入頁面！"
            )

        print("[Action Agent] Portal session 建立完成。")

    async def _login_interactively(self):
        """用可見瀏覽器完成需要使用者操作的 Portal 登入。"""

        assert self.playwright is not None

        browser_ui = await self.playwright.chromium.launch(
            headless=False
        )

        try:
            context_ui = await browser_ui.new_context(
                locale="zh-TW"
            )

            page_ui = await context_ui.new_page()

            print("[Action Agent] 正在開啟中大 Portal...")

            await page_ui.goto(PORTAL_LOGIN_URL)

            await page_ui.get_by_role(
                "textbox",
                name="帳號",
            ).fill(self.username)

            await page_ui.get_by_role(
                "textbox",
                name="密碼",
            ).fill(self.password)

            print(
                "\n=======================================================\n"
                "[Action Agent 暫停]\n"
                "請手動打勾「我不是機器人」並解題。\n"
                "完成後請手動點擊「登入 Portal」按鈕，"
                "系統將等待 90 秒...\n"
                "=======================================================\n"
            )

            await page_ui.get_by_role(
                "button",
                name="登入 Portal",
            ).wait_for(
                state="hidden",
                timeout=90000,
            )

            print("[Action Agent] Portal 登入成功！")
            print("[Action Agent] 正在檢查是否有「修改密碼」提示...")

            try:
                cancel_btn = page_ui.get_by_role(
                    "button",
                    name="關閉",
                )

                await cancel_btn.wait_for(
                    state="visible",
                    timeout=2000,
                )

                await cancel_btn.click()

                await page_ui.wait_for_timeout(1000)

            except Exception:
                pass

            print(
                "[Action Agent] 等待 Portal 首頁載入完成，"
                "寫入憑證..."
            )

            await page_ui.wait_for_selector(
                "text=學生服務",
                state="visible",
                timeout=15000,
            )

            await page_ui.wait_for_load_state("networkidle")

            real_user_agent = await page_ui.evaluate(
                "navigator.userAgent"
            )

            login_state = await context_ui.storage_state()

            print(
                "[Action Agent] 畫面關閉，已擷取登入憑證，"
                "準備轉入背景執行..."
            )

            return login_state, real_user_agent

        except Exception as exc:
            raise RuntimeError(
                f"Portal 登入階段發生錯誤: {exc}"
            ) from exc

        finally:
            await browser_ui.close()

    async def open_portal(self) -> Page:
        """取得已登入的 Portal page。"""

        if self.page is None:
            raise RuntimeError(
                "PortalSession 尚未啟動，請先呼叫 start()。"
            )

        return self.page

    async def open_course_system(self) -> Page:
        """從 Portal 導航至課務系統，並回傳課務系統 page。

        課務系統仍然維持原本從 Portal 進入的方式。
        """

        if self.page is None:
            raise RuntimeError(
                "PortalSession 尚未啟動，請先呼叫 start()。"
            )

        if self.context is None:
            raise RuntimeError(
                "BrowserContext 尚未建立。"
            )

        if self.course_mgr_page is not None:
            return self.course_mgr_page

        print("[Action Agent] 正在背景導航至課務系統...")

        await self.page.get_by_text(
            "學生服務",
            exact=False,
        ).first.click()

        await self.page.wait_for_timeout(200)

        await self.page.get_by_text(
            "教務相關服務",
            exact=False,
        ).first.click()

        await self.page.wait_for_timeout(200)

        async with self.context.expect_page() as new_page_info:
            await self.page.get_by_text(
                "課務系統",
                exact=False,
            ).first.click()

        self.course_mgr_page = await new_page_info.value

        await self.course_mgr_page.wait_for_load_state(
            "networkidle"
        )

        return self.course_mgr_page

    async def open_registration_system(self) -> Page:
        """直接登入獨立的 NCU 選課系統。

        選課系統與 Portal 完全獨立：

        /Course/main/login
            ↓
        account + passwd
            ↓
        登入
            ↓
        /Course/main/sign/selectCourse?step=3
        """

        if self.context is None:
            raise RuntimeError(
                "BrowserContext 尚未建立，請先呼叫 start()。"
            )

        # 已經建立過選課 page，就直接重用
        if self.registration_page is not None:
            if not self.registration_page.is_closed():
                return self.registration_page

        print(
            "[Action Agent] 正在開啟 NCU 選課系統登入頁..."
        )

        self.registration_page = await self.context.new_page()

        page = self.registration_page

        # ========================================================
        # 1. 開啟登入頁
        # ========================================================

        await page.goto(
            REGISTRATION_LOGIN_URL,
            wait_until="networkidle",
        )

        print(
            "[Registration] 登入頁 URL:",
            page.url,
        )

        # ========================================================
        # 2. 確認帳號密碼欄位存在
        # ========================================================

        account_input = page.locator(
            'input[name="account"]'
        )

        password_input = page.locator(
            'input[name="passwd"]'
        )

        await account_input.wait_for(
            state="visible",
            timeout=10000,
        )

        await password_input.wait_for(
            state="visible",
            timeout=10000,
        )

        print(
            "[Registration] 找到帳號 / 密碼欄位"
        )

        # ========================================================
        # 3. 填寫帳號
        # ========================================================

        await account_input.fill(
            self.username
        )

        # ========================================================
        # 4. 填寫密碼
        # ========================================================

        await password_input.fill(
            self.password
        )

        print(
            "[Registration] 帳號密碼已填入"
        )

        # ========================================================
        # 5. 點擊登入
        #
        # HTML:
        #
        # <input
        #     type="submit"
        #     name="submit"
        #     value="登入"
        #     tabindex="3"
        # >
        # ========================================================

        login_button = page.locator(
            'input[type="submit"][name="submit"][value="登入"]'
        )

        await login_button.wait_for(
            state="visible",
            timeout=5000,
        )

        print(
            "[Registration] 正在按下「登入」..."
        )

        await login_button.click()

        # ========================================================
        # 6. 等待登入請求完成
        # ========================================================

        try:
            await page.wait_for_load_state(
                "networkidle",
                timeout=15000,
            )
        except Exception:
            print(
                "[Registration] networkidle timeout，"
                "繼續檢查頁面..."
            )

        await page.wait_for_timeout(1000)

        print(
            "[Registration] 登入後 URL:",
            page.url,
        )

        # ========================================================
        # 7. 印出登入後頁面基本資訊
        #
        # 這部分是為了 debug。
        # ========================================================

        print(
            "[Registration] 登入後 Title:",
            await page.title(),
        )

        print(
            "[Registration] 登入後是否仍有 account 欄位:",
            await page.locator(
                'input[name="account"]'
            ).count(),
        )

        print(
            "[Registration] 登入後是否仍有 passwd 欄位:",
            await page.locator(
                'input[name="passwd"]'
            ).count(),
        )

        # ========================================================
        # 8. 不再直接用 URL 判斷登入成功
        #
        # 即使登入後仍然是：
        #
        # /Course/main/login
        #
        # 也先不要判定失敗。
        # ========================================================

        print(
            "[Registration] 嘗試直接進入選課頁..."
        )

        await page.goto(
            REGISTRATION_HOME_URL,
            wait_until="networkidle",
        )

        await page.wait_for_timeout(1000)

        print(
            "[Registration] 選課頁導向後 URL:",
            page.url,
        )

        print(
            "[Registration] 選課頁 Title:",
            await page.title(),
        )

        # ========================================================
        # 9. 判斷是否真的被踢回登入頁
        # ========================================================

        if "/Course/main/login" in page.url:

            print(
                "[Registration] 進入選課頁後仍被導回登入頁"
            )

            # Debug 資訊
            print(
                "[Registration] 最終 URL:",
                page.url,
            )

            print(
                "[Registration] 最終 Title:",
                await page.title(),
            )

            print(
                "[Registration] account 欄位數量:",
                await page.locator(
                    'input[name="account"]'
                ).count(),
            )

            print(
                "[Registration] passwd 欄位數量:",
                await page.locator(
                    'input[name="passwd"]'
                ).count(),
            )

            # 把登入頁 HTML 前面一部分印出來
            html = await page.locator(
                "body"
            ).inner_text()

            print(
                "\n========== 選課系統目前頁面內容 ==========\n"
            )

            print(html[:3000])

            print(
                "\n===========================================\n"
            )

            raise RuntimeError(
                "選課系統登入失敗："
                "進入 selectCourse 後被重新導回登入頁。"
            )

        # ========================================================
        # 10. 確認確實進入 selectCourse
        # ========================================================

        if "selectCourse" not in page.url:

            print(
                "[Registration] 沒有進入預期的 selectCourse 頁面"
            )

            print(
                "[Registration] 目前 URL:",
                page.url,
            )

            raise RuntimeError(
                "選課系統登入後沒有進入預期的選課頁面。"
                f"目前 URL: {page.url}"
            )

        print(
            "[Registration] "
            "========================================"
        )

        print(
            "[Registration] 選課系統登入成功！"
        )

        print(
            "[Registration] "
            "已進入 selectCourse?step=3"
        )

        print(
            "[Registration] URL:",
            page.url,
        )

        print(
            "[Registration] "
            "========================================"
        )

        return page

    async def close(self):
        """關閉 browser / playwright 資源。"""

        if self.browser is not None:
            await self.browser.close()
            self.browser = None

        if self.playwright is not None:
            await self.playwright.stop()
            self.playwright = None

        self.context = None
        self.page = None
        self.course_mgr_page = None
        self.registration_page = None





import asyncio
import json
import re
import sqlite3
from typing import Any, Optional
from playwright.async_api import BrowserContext, Page

# ==================================================
# 1. 課表代碼轉換與衝堂比對核心
# ==================================================

PERIOD_MAP = {
    "第1節": "1", "第一節": "1",
    "第2節": "2", "第二節": "2",
    "第3節": "3", "第三節": "3",
    "第4節": "4", "第四節": "4",
    "第Z節": "Z", "第z節": "Z",
    "第5節": "5", "第五節": "5",
    "第6節": "6", "第六節": "6",
    "第7節": "7", "第七節": "7",
    "第8節": "8", "第八節": "8",
    "第9節": "9", "第九節": "9",
    "第A節": "A", "第a節": "A",
    "第B節": "B", "第b節": "B",
    "第C節": "C", "第c節": "C",
    "第D節": "D", "第d節": "D",
}

DAY_MAP = {
    "星期一": "一", "週一": "一", "一": "一",
    "星期二": "二", "週二": "二", "二": "二",
    "星期三": "三", "週三": "三", "三": "三",
    "星期四": "四", "週四": "四", "四": "四",
    "星期五": "五", "週五": "五", "五": "五",
    "星期六": "六", "週六": "六", "六": "六",
    "星期日": "日", "週日": "日", "日": "日",
}


def build_schedule_occupied_slots(schedule_data: list[dict[str, Any]]) -> set[str]:
    """將 get_schedule() 回傳的課表轉為佔用時段集合 (例如: {'一2', '一3', '四5'})。"""
    occupied_slots = set()
    for item in schedule_data:
        day_str = DAY_MAP.get(item.get("day", "").strip(), "")
        period_raw = item.get("period", "").strip()
        period_code = PERIOD_MAP.get(
            period_raw, period_raw.replace("第", "").replace("節", "")
        )

        if day_str and period_code:
            occupied_slots.add(f"{day_str}{period_code.upper()}")

    return occupied_slots


def parse_course_time_slots(time_str: str) -> set[str]:
    """將時間字串（例如 '一234', '四ABC', '一567/I-015'）解析為時段集合。"""
    slots = set()
    if not time_str or "學分" in time_str:
        return slots

    matches = re.findall(r"([一二三四五六日])([0-9A-Za-z]+)", time_str)
    for day, periods in matches:
        for p in periods:
            slots.add(f"{day}{p.upper()}")

    return slots


def filter_available_courses(
    courses: list[dict[str, Any]], occupied_slots: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """過濾出無衝堂的課程，回傳 (可選清單, 衝堂清單)。"""
    available_courses = []
    conflicted_courses = []

    for course in courses:
        course_slots = parse_course_time_slots(course.get("time", ""))
        conflict_slots = course_slots & occupied_slots

        if conflict_slots:
            course_copy = dict(course)
            course_copy["conflict_reason"] = (
                f"與已選課表衝堂: {sorted(list(conflict_slots))}"
            )
            conflicted_courses.append(course_copy)
        else:
            available_courses.append(course)

    return available_courses, conflicted_courses


# ==================================================
# 2. 公開課程爬蟲（含非同步課綱批次下載）
# ==================================================

async def _fetch_single_outline(context: BrowserContext, course: dict[str, Any]) -> dict[str, Any]:
    """在新分頁載入課綱網址並抓取前段純文字。"""
    url = course.get("outline_url")
    if not url:
        return course

    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=12000)
        # 取得課綱內文並截取前 500 字摘要
        content = await page.locator("body").inner_text()
        course["outline_text"] = content.strip()[:500]
    except Exception:
        course["outline_text"] = ""
    finally:
        await page.close()

    return course


async def query_courses_by_keywords(
    page: Page, keyword: str, fetch_outlines: bool = False
) -> list[dict[str, Any]]:
    """從公開查詢系統爬取課程。可選擇是否並行抓取課綱。"""
    url = "https://cis.ncu.edu.tw/Course/main/query/byKeywords"
    await page.goto(url, wait_until="domcontentloaded")

    search_input = page.locator(
        'input[name="keyWord"], #keyWord, input[type="text"]'
    ).first
    await search_input.fill(keyword)

    submit_btn = page.locator(
        'input[type="submit"], button:has-text("查詢"), button:has-text("Search")'
    ).first
    await submit_btn.click()

    table = page.locator("table.table, table").last
    await table.wait_for(state="visible", timeout=10000)

    rows = table.locator("tbody tr")
    row_count = await rows.count()
    courses = []

    for i in range(row_count):
        row = rows.nth(i)
        tds = row.locator("td")
        td_count = await tds.count()
        if td_count < 6:
            continue

        col0_text = (await tds.nth(0).inner_text()).strip()
        lines = [line.strip() for line in col0_text.splitlines() if line.strip()]
        serial = lines[0] if len(lines) > 0 else ""
        course_no = "".join(lines[1:]) if len(lines) > 1 else ""

        title = (await tds.nth(1).inner_text()).strip().splitlines()[0]
        teacher = (await tds.nth(2).inner_text()).strip()
        credits_val = (await tds.nth(3).inner_text()).strip()

        raw_time_room = (await tds.nth(4).inner_text()).strip()
        time_match = re.search(r"([一二三四五六日][0-9A-Za-z]+)", raw_time_room)
        time_slot = time_match.group(1) if time_match else raw_time_room

        # 抓取課綱連結
        outline_link = row.locator("a:has-text('課程綱要')")
        outline_url = ""
        if await outline_link.count() > 0:
            onclick_attr = await outline_link.first.get_attribute("onclick")
            m = re.search(r"open_outline\('([^']+)'\)", onclick_attr or "")
            if m:
                outline_url = f"https://cis.ncu.edu.tw{m.group(1)}"

        courses.append({
            "serial": serial,
            "course_no": course_no,
            "title": title,
            "teacher": teacher,
            "time": time_slot,
            "credits": credits_val,
            "raw_info": raw_time_room,
            "outline_url": outline_url,
            "outline_text": "",
        })

    # 並行抓取課綱（限制 3 個 Worker 避免過載）
    if fetch_outlines and page.context:
        sem = asyncio.Semaphore(3)

        async def sem_fetch(c):
            async with sem:
                return await _fetch_single_outline(page.context, c)

        courses = await asyncio.gather(*(sem_fetch(c) for c in courses))

    return courses


# ==================================================
# 3. 本地 SQLite 快取管理
# ==================================================

DB_PATH = "courses.db"

def init_db():
    """初始化儲存課程與個人課表的 SQLite 資料庫"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS courses (
                serial TEXT PRIMARY KEY,
                course_no TEXT,
                title TEXT,
                teacher TEXT,
                time TEXT,
                credits TEXT,
                outline_text TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS student_schedule (
                slot TEXT PRIMARY KEY
            )
        """)
        conn.commit()


def save_student_slots(occupied_slots: set[str]):
    """快取學生目前課表佔用的時段"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM student_schedule")
        cursor.executemany(
            "INSERT OR REPLACE INTO student_schedule (slot) VALUES (?)",
            [(slot,) for slot in occupied_slots],
        )
        conn.commit()


def get_student_slots() -> set[str]:
    """自資料庫讀取已儲存的學生時段"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT slot FROM student_schedule")
        return {r[0] for r in cursor.fetchall()}


def save_courses_to_db(courses: list[dict[str, Any]]):
    """將爬取下來的課程寫入或更新至資料庫"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        for c in courses:
            cursor.execute("""
                INSERT OR REPLACE INTO courses (serial, course_no, title, teacher, time, credits, outline_text)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                c.get("serial", ""),
                c.get("course_no", ""),
                c.get("title", ""),
                c.get("teacher", ""),
                c.get("time", ""),
                c.get("credits", ""),
                c.get("outline_text", ""),
            ))
        conn.commit()


# ==================================================
# 4. Agentic AI 工具介面 (Tools / Function Calling)
# ==================================================

def tool_search_available_courses(
    keyword: str,
    max_credits: Optional[float] = None
) -> str:
    """
    【Agent Tool】根據關鍵字搜尋課程，並自動依據當前學生的課表排除衝堂課程。

    :param keyword: 想搜尋的課名關鍵字，如「日文」、「資料結構」、「人工智慧」
    :param max_credits: 學分上限篩選（可選）
    :return: 包含可選課程與衝堂課程列表的 JSON 字串
    """
    occupied_slots = get_student_slots()

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT serial, course_no, title, teacher, time, credits FROM courses WHERE title LIKE ?",
            (f"%{keyword}%",),
        )
        rows = cursor.fetchall()

    if not rows:
        return json.dumps(
            {"status": "empty", "message": f"資料庫中暫無包含「{keyword}」的課程資料。"},
            ensure_ascii=False,
        )

    courses = [
        {"serial": r[0], "course_no": r[1], "title": r[2], "teacher": r[3], "time": r[4], "credits": r[5]}
        for r in rows
    ]

    available, conflicts = filter_available_courses(courses, occupied_slots)

    # 處理學分篩選（含小數容錯）
    if max_credits is not None:
        def safe_float(val: str) -> float:
            try:
                return float(val)
            except ValueError:
                return 0.0

        available = [c for c in available if safe_float(c["credits"]) <= float(max_credits)]

    return json.dumps({
        "status": "success",
        "student_occupied_slots": sorted(list(occupied_slots)),
        "available_count": len(available),
        "available_courses": available,
        "conflicts_count": len(conflicts),
        "conflicted_courses": conflicts,
    }, ensure_ascii=False)


def tool_check_course_syllabus(course_title_or_serial: str) -> str:
    """
    【Agent Tool】查詢指定課程的綱要、評分標準與上課目標。

    :param course_title_or_serial: 課程名稱或流水號（例如 "00002" 或 "日文(一)A"）
    :return: 課綱內文 JSON 字串
    """
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT title, teacher, time, credits, outline_text FROM courses WHERE title LIKE ? OR serial = ? LIMIT 1",
            (f"%{course_title_or_serial}%", course_title_or_serial),
        )
        row = cursor.fetchone()

    if not row:
        return json.dumps(
            {"status": "error", "message": f"找不到課程「{course_title_or_serial}」的詳細課綱。"},
            ensure_ascii=False,
        )

    return json.dumps({
        "status": "success",
        "title": row[0],
        "teacher": row[1],
        "time": row[2],
        "credits": row[3],
        "syllabus": row[4] if row[4] else "該課程無課綱或尚未抓取內文",
    }, ensure_ascii=False)
async def get_schedule(session: NCUSession):
    """Action：取得目前個人課表。"""
    # 透過 Portal 跳轉至課務系統
    course_mgr_page = await session.open_course_system()

    # 解析課表並回傳
    return await parse_ncu_schedule_table(course_mgr_page)