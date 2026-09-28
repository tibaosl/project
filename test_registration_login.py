"""選課系統登入失敗偵測。

用 Playwright route 回假頁面，不會真的連到選課系統。
"""

import asyncio

import pytest
from playwright.async_api import async_playwright

from action_tools import NCUSession, RegistrationUnavailableError

LOGIN_PAGE = """<html><body><form method="post" action="/Course/main/login">
<input name="account"><input name="passwd" type="password">
<input type="submit" name="submit" value="登入">
</form></body></html>"""

# 真實選課頁沒登入時的樣子：網址不變，只顯示要重新登入
RELOGIN_PAGE = """<html><head><title>新選課登記系統</title></head><body>
[ 非選課階段 ] 您可能因為閒置時間過長, 需要重新登入, 稍後為您重導登入網址
</body></html>"""

SELECT_COURSE_WITHOUT_SEARCH = """<html><head><title>新選課登記系統</title></head><body>
[ 非選課階段 ] 歡迎使用選課系統
</body></html>"""


def test_no_password_fails_immediately():
    session = NCUSession("student", "")
    session.context = object()  # 只需要通過「有沒有 start()」的檢查，不會真的用到

    with pytest.raises(RegistrationUnavailableError, match="分開登入"):
        asyncio.run(session.open_registration_system())


async def _run_with_fake_registration(select_course_html, action):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()

        async def handle(route):
            if "/Course/main/login" in route.request.url:
                if route.request.method == "POST":
                    await route.fulfill(status=302, headers={"Location": "/Course/main/login"})
                else:
                    await route.fulfill(content_type="text/html; charset=utf-8", body=LOGIN_PAGE)
            else:
                await route.fulfill(content_type="text/html; charset=utf-8", body=select_course_html)

        await context.route("https://cis.ncu.edu.tw/**", handle)
        session = NCUSession("student", "wrong-password")
        session.context = context
        try:
            return await action(session)
        finally:
            await browser.close()


def test_relogin_page_counts_as_login_failure():
    async def action(session):
        with pytest.raises(RegistrationUnavailableError, match="登入失敗"):
            await session.open_registration_system()
        return session.registration_page

    # 失敗的分頁要清掉，下次才不會被當成已登入重用
    assert asyncio.run(_run_with_fake_registration(RELOGIN_PAGE, action)) is None


def test_missing_keyword_search_reports_non_selection_period():
    from action_tools import search_courses

    async def action(session):
        with pytest.raises(RegistrationUnavailableError, match="非選課階段"):
            await search_courses(session, "日文")

    asyncio.run(_run_with_fake_registration(SELECT_COURSE_WITHOUT_SEARCH, action))
