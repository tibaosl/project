"""iNCU 服務櫃台 - 活動報名系統 相關工具。

對應網站：https://cis.ncu.edu.tw/iNCU/publicService/activityQuery

這一層只處理「公開、不需登入」就能看到的活動查詢與活動詳情，
使用 requests + BeautifulSoup 直接打服務端渲染的 HTML（活動查詢是單純的
GET + query string，不是 AJAX API），跟 crawler_tools.py 的作法一致。

需要登入才能做的事（例如實際送出報名、查看個人時數 dashboard），
請見 action_tools.py 裡 NCUSession 的 iNCU 相關方法。
"""

from typing import Any, Optional
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ACTIVITY_QUERY_URL = "https://cis.ncu.edu.tw/iNCU/publicService/activityQuery"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    )
}

# 對應網頁上「活動類別」下拉選單的 value
ACTIVITY_CATEGORY_MAP = {
    "藝文活動": "1",
    "電影/表演欣賞": "2",
    "資訊專業": "3",
    "演講/講座": "4",
    "其他": "5",
    "學生社團活動": "6",
    "說明會/講習": "7",
    "分享會/座談會": "8",
    "訓練/工作坊": "9",
    "課程": "10",
    "研習/研討": "11",
    "展覽/展演": "12",
    "教職員活動": "13",
    "校外活動": "14",
    "諮詢": "15",
    "測驗/解測": "16",
    "競賽": "17",
    "典禮": "18",
}

# 對應網頁上「參與對象」下拉選單的 value
ACTIVITY_TARGET_MAP = {
    "學生": "STUDENT",
    "教職員": "FACULTY",
    "校外人士": "NETIDISREAD",
}


def search_activities(
    keyword: str = "",
    category: str = "",
    target: str = "",
    department_code: str = "",
    open_signup_only: bool = False,
    start_date: str = "",
    end_date: str = "",
) -> list[dict[str, Any]]:
    """依條件搜尋 iNCU 活動報名系統的公開活動列表。

    Args:
        keyword: 活動名稱關鍵字。
        category: 活動類別中文名稱（見 ACTIVITY_CATEGORY_MAP），
            也可以直接傳 value（例如 "6"）。
        target: 參與對象中文名稱（見 ACTIVITY_TARGET_MAP），
            也可以直接傳 value（例如 "STUDENT"）。
        department_code: 承辦單位代碼（例如 "A420" 課外活動組），
            代碼需對照網頁上的下拉選單，這裡不窮舉。
        open_signup_only: 是否只顯示「開放報名中」的活動。
        start_date / end_date: 篩選活動日期區間，格式 "YYYY-MM-DD"。

    Returns:
        每筆活動的摘要資訊（activity_id、title、status、target_audience、
        hours_tags、url）。詳細場次資訊請用 get_activity_detail()。
    """

    category_value = ACTIVITY_CATEGORY_MAP.get(category, category)
    target_value = ACTIVITY_TARGET_MAP.get(target, target)

    params = {
        "filter_name": keyword,
        "filter_type": category_value,
        "filter_participant": target_value,
        "filter_unit": department_code,
        "filter_open_signup": "Y" if open_signup_only else "",
        "filter_start_date": start_date,
        "filter_end_date": end_date,
    }
    params = {k: v for k, v in params.items() if v}

    print(f"[Activity Tools] 查詢活動列表，條件：{params}")

    response = requests.get(
        ACTIVITY_QUERY_URL,
        params=params,
        headers=HEADERS,
        verify=False,
        timeout=15,
    )
    response.raise_for_status()
    response.encoding = "utf-8"

    soup = BeautifulSoup(response.text, "html.parser")

    thumbnail_container = soup.select_one("#activityThumbnail")
    cards = (
        thumbnail_container.select("[data-activity-id]")
        if thumbnail_container
        else soup.select("[data-activity-id]")
    )

    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for card in cards:
        activity_id = card.get("data-activity-id")
        if not activity_id or activity_id in seen_ids:
            continue
        seen_ids.add(activity_id)

        title_tag = card.select_one(".card-title")
        title = (
            title_tag.get("title", "").strip()
            if title_tag and title_tag.get("title")
            else (title_tag.get_text(strip=True) if title_tag else "")
        )

        status_badge = card.select_one(".badge")
        status = status_badge.get_text(strip=True) if status_badge else ""

        link_tag = card.select_one(f"a[href*='activityQuery/{activity_id}']")
        link = (
            link_tag.get("href")
            if link_tag
            else urljoin(ACTIVITY_QUERY_URL + "/", activity_id)
        )

        target_audience: list[str] = []
        hours_tags: list[str] = []

        for text_block in card.select(".card-text"):
            label = text_block.get_text(strip=True)
            if label.startswith("參與對象"):
                target_audience = [
                    b.get_text(strip=True) for b in text_block.select(".badge")
                ]
            elif label.startswith("時數標籤"):
                tag_text = label.replace("時數標籤：", "").replace("時數標籤", "").strip()
                if tag_text:
                    hours_tags = [t.strip() for t in tag_text.split() if t.strip()]

        results.append(
            {
                "activity_id": activity_id,
                "title": title,
                "status": status,
                "target_audience": target_audience,
                "hours_tags": hours_tags,
                "url": link,
            }
        )

    print(f"[Activity Tools] 共找到 {len(results)} 筆活動。")

    return results


def get_activity_detail(activity_id: str) -> dict[str, Any]:
    """取得單一活動的詳細資訊，包含所有場次(session)資料。

    對應網頁：https://cis.ncu.edu.tw/iNCU/publicService/activityQuery/{activity_id}
    """

    url = f"{ACTIVITY_QUERY_URL}/{activity_id}"

    print(f"[Activity Tools] 查詢活動詳情：{url}")

    response = requests.get(url, headers=HEADERS, verify=False, timeout=15)
    response.raise_for_status()
    response.encoding = "utf-8"

    soup = BeautifulSoup(response.text, "html.parser")

    info: dict[str, Any] = {"activity_id": activity_id, "url": url}

    main_dl = soup.select_one("dl.row")
    if main_dl:
        for dt in main_dl.find_all("dt", recursive=False):
            label = dt.get_text(strip=True)
            dd = dt.find_next_sibling("dd")
            if not dd:
                continue
            value = dd.get_text(strip=True)
            if label == "活動名稱":
                info["title"] = value
            elif label == "承辦單位":
                info["department"] = value
            elif label == "承辦人":
                info["contact_person"] = value
            elif label == "承辦人e-mail":
                info["contact_email"] = value

    sessions: list[dict[str, Any]] = []

    for pane in soup.select("div.tab-pane"):
        session: dict[str, Any] = {"session_id": pane.get("id", "")}

        signup_button = pane.select_one("a.btn")
        session["requires_login_to_register"] = bool(
            signup_button and "login" in (signup_button.get("href") or "")
        )

        signup_status = pane.select_one("p.text-info")
        if signup_status:
            session["signup_status_text"] = signup_status.get_text(" ", strip=True)

        title_h3 = pane.select_one("h3")
        if title_h3:
            session["session_name"] = title_h3.get_text(strip=True)

        session_dl = pane.select_one("dl.row")
        if session_dl:
            for dt in session_dl.find_all("dt", recursive=False):
                label = dt.get_text(strip=True)
                dd = dt.find_next_sibling("dd")
                if not dd:
                    continue
                value = dd.get_text(strip=True)

                field_map = {
                    "講師": "instructor",
                    "地點": "location",
                    "報名時間": "signup_period",
                    "場次時間": "event_period",
                    "報名人數上限": "capacity",
                    "備取人數上限": "waitlist_capacity",
                    "時數標籤": "hours_tag",
                    "學習護照時數標籤": "passport_hours_tag",
                    "軟實力時數標籤": "soft_skill_hours_tag",
                }
                field = field_map.get(label)
                if field:
                    session[field] = value

        sessions.append(session)

    info["sessions"] = sessions

    print(
        f"[Activity Tools] 「{info.get('title', activity_id)}」"
        f"共有 {len(sessions)} 個場次。"
    )

    return info


if __name__ == "__main__":
    # 簡單手動測試：搜尋 + 看第一筆的詳情
    found = search_activities(keyword="糖霜")
    for item in found:
        print(item)

    if found:
        detail = get_activity_detail(found[0]["activity_id"])
        print("\n=== 詳情 ===")
        print(detail)
