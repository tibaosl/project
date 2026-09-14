"""iNCU 服務櫃台 - 活動報名系統 相關工具。

對應網站：https://cis.ncu.edu.tw/iNCU/publicService/activityQuery

這一層只處理「公開、不需登入」就能看到的活動查詢與活動詳情，
使用 requests + BeautifulSoup 直接打服務端渲染的 HTML（活動查詢是單純的
GET + query string，不是 AJAX API），跟 crawler_tools.py 的作法一致。

需要登入才能做的事（例如實際送出報名、查看個人時數 dashboard），
請見 action_tools.py 裡 NCUSession 的 iNCU 相關方法。
"""

import re
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

# 對應「進階搜尋」裡「時數標籤」欄位的 filter_tag 值（格式：mainTag_subTag）。
# 這是「學習護照時數標籤」在網站後台的標籤代碼，用瀏覽器開發者工具在
# 進階搜尋的標籤下拉選單裡點開「服務學習發展中心」分類後，逐一點擊每個
# 標籤按鈕、讀取其 data-main-tag / data-sub-tag 屬性後記錄下來的
# （這組代碼不太可能常態變動，但如果校方調整了標籤分類，需要重新抓）。
#
# 有這組代碼，就能直接請伺服器端依標籤篩選活動列表，不用像
# _scan_open_activities_for_tags() 原本的做法一樣，掃過所有開放報名中
# 的活動、逐一打 get_activity_detail() 檢查有沒有該標籤——那種做法在
# 標籤稀有時要掃過大量完全不相關的活動（例如 Zumba 社課、輔導股長訓練），
# 非常浪費。
STUDY_PASSPORT_TAG_FILTER_MAP = {
    "大一週會": "217_443",
    "院週會": "217_444",
    "大一CPR": "217_445",
    "自我探索與生涯規劃": "217_446",
    "其他生活知能": "217_447",
    "服務學習課程": "218_448",
    "校外服務": "218_449",
    "人文藝術": "219_442",
    "國際視野": "220_452",
}


def search_activities(
    keyword: str = "",
    category: str = "",
    target: str = "",
    department_code: str = "",
    open_signup_only: bool = False,
    start_date: str = "",
    end_date: str = "",
    tag_filter: str = "",
    page: int = 1,
) -> list[dict[str, Any]]:
    """依條件搜尋 iNCU 活動報名系統的公開活動列表（單頁，每頁 20 筆）。

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
        tag_filter: 「進階搜尋」裡「時數標籤」欄位的值，格式
            "mainTag_subTag"（見 STUDY_PASSPORT_TAG_FILTER_MAP）。
            這是伺服器端直接支援的篩選，比自己抓全部活動再逐一檢查
            passport_hours_tag 快很多，找學習護照時數類別的活動時優先用這個。
        page: 頁碼（對應網頁的 ?page=N，從 1 開始）。網站一頁固定 20 筆，
            這個函式只抓「這一頁」；要抓全部頁面請用 search_all_activities()。

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
        "filter_tag": tag_filter,
    }
    params = {k: v for k, v in params.items() if v}
    if page > 1:
        params["page"] = page

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


def search_all_activities(
    max_pages: int = 50,
    **filters: Any,
) -> list[dict[str, Any]]:
    """依條件搜尋活動，自動翻頁抓完所有頁面（不只第一頁的 20 筆）。

    網站一頁固定 20 筆，之前掃描活動只抓了 search_activities() 的第一頁，
    範圍不夠完整。這裡用同樣的篩選條件，從 page=1 開始一直往後抓，
    抓到某一頁沒有結果就停下來。

    Args:
        max_pages: 安全上限，避免網站行為異常時無限翻頁下去
            （目前實測全部活動大約 5 頁，50 頁已經是很寬鬆的上限）。
        **filters: 直接轉傳給 search_activities() 的篩選條件
            （keyword、category、target、department_code、
            open_signup_only、start_date、end_date）。

    Returns:
        所有頁面活動摘要的合併清單（依 activity_id 去重）。
    """

    all_results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for page in range(1, max_pages + 1):
        page_results = search_activities(page=page, **filters)
        if not page_results:
            break

        new_count = 0
        for item in page_results:
            if item["activity_id"] not in seen_ids:
                seen_ids.add(item["activity_id"])
                all_results.append(item)
                new_count += 1

        # 這一頁的活動都已經看過（代表已經翻到最後、開始重複），就停下來，
        # 避免網站分頁行為異常時卡在原地一直重複抓同一頁。
        if new_count == 0:
            break

    print(f"[Activity Tools] 翻頁掃描完成，共 {len(all_results)} 筆活動。")

    return all_results


def _extract_rich_text(container) -> str:
    """把「活動內容」那種富文字區塊轉成乾淨的純文字。

    這種內容通常是從 Word 貼上來的 HTML，同一段文字裡的數字/英文
    常被拆成一堆零碎的 <span>。逐段（<p>）取文字、段落內不加分隔符
    （span 裡該有的空格本來就在文字節點裡），只在段落之間換行；
    <br> 另外轉成換行符號保留段落內部的換行（例如「時間：...\n地點：...」）。
    """

    if container is None:
        return ""

    for br in container.find_all("br"):
        br.replace_with("\n")

    def _clean(text: str) -> str:
        # 只壓縮空白跟 tab，不要動到 \n（那是我們自己特意保留的段落/換行）。
        return re.sub(r"[ \t]+", " ", text).strip()

    # 段落用 <p>，條列式內容常見用 <ul>/<li>（例如活動場次用清單列出），
    # 兩種都當作獨立一行處理；find_all(["p", "li"]) 會照文件順序回傳。
    blocks = container.find_all(["p", "li"])
    if blocks:
        lines = [_clean(b.get_text("")) for b in blocks]
        return "\n".join(line for line in lines if line)

    return _clean(container.get_text(""))


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

        # 「活動內容」這個 dt 後面接的是一個空的 dd（class col-sm-10），
        # 真正的說明文字放在再下一個 dd（class col-sm-12）裡，不是用
        # 一般的 dt/dd 一對一配對，所以要另外抓，抓不到就是空字串。
        #
        # 這段內容通常是從 Word 貼上來的 HTML，同一段文字裡的數字/英文
        # 常被拆成一堆零碎的 <span>（Word 貼上的常見產物）。如果直接用
        # get_text(separator) 會在每個 span 之間硬塞分隔符號，把
        # 「2026年第13屆」拆成「2026 年第 13 屆」這種破碎的樣子——
        # 所以改成逐段（<p>）取文字、段落內不加分隔符（span 裡該有的
        # 空格本來就在文字節點裡，直接接起來就對了），只在段落「之間」
        # 換行；<br> 另外轉成換行符號保留段內的換行。
        content_dd = main_dl.select_one("dd.col-sm-12")
        info["description"] = _extract_rich_text(content_dd)

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

        # -------------------------------------------------------------
        # 判斷這個場次是「線上報名」還是「現場報名」。
        #
        # 觀察到的規律（已用真實案例「107影享會」電影場次驗證）：
        # 現場報名的場次，「報名時間」欄位的起訖時間會完全相同
        # （例如 "2026-09-15 18:00 ~ 2026-09-15 18:00"），代表系統上
        # 根本沒有開放事先報名的窗口，只是把活動開始時間重複填了兩次
        # 當佔位——這種場次要嘛看活動內容說明現場怎麼報到，要嘛就是
        # 系統紀錄用、不需要使用者自己動作。
        # -------------------------------------------------------------
        signup_period = session.get("signup_period", "")
        if "~" in signup_period:
            start, end = (p.strip() for p in signup_period.split("~", 1))
            session["registration_mode"] = (
                "onsite" if start and start == end else "online"
            )
        else:
            session["registration_mode"] = "unknown"

        sessions.append(session)

    info["sessions"] = sessions

    print(
        f"[Activity Tools] 「{info.get('title', activity_id)}」"
        f"共有 {len(sessions)} 個場次。"
    )

    return info


def _scan_activities_for_tag(
    tag_name: str,
    max_candidates: Optional[int],
    limit: Optional[int],
    start_index: int,
) -> tuple[list[dict[str, Any]], int, bool]:
    """單一標籤的核心掃描邏輯。

    優先用伺服器端的「時數標籤」篩選（STUDY_PASSPORT_TAG_FILTER_MAP）
    直接縮小候選活動範圍——例如篩「人文藝術」伺服器只會回傳 10 筆左右，
    而不是像以前那樣要掃過全部 40~50 筆開放報名中的活動（其中大多數
    跟目標標籤完全無關，例如 Zumba 社課、輔導股長訓練）。如果標籤不在
    對照表裡（代碼未知），才退回掃全部活動、逐一用 get_activity_detail()
    檢查 passport_hours_tag 的舊做法。

    每筆場次資訊都會附上報名人數/名額（capacity、waitlist_capacity、
    signup_status_text），因為使用者選活動時最在意的就是還有沒有名額，
    不能只給活動資訊卻不給名額，等到真的要報名才發現額滿。

    Returns:
        (items, next_index, exhausted) — next_index/exhausted 的意義：
        下次要從候選清單第幾筆繼續掃描、是否已經掃到候選清單最後一筆。
    """

    tag_filter = STUDY_PASSPORT_TAG_FILTER_MAP.get(tag_name, "")

    if tag_filter:
        candidates = search_all_activities(open_signup_only=True, tag_filter=tag_filter)
    else:
        print(f"[Activity Tools] 標籤「{tag_name}」沒有已知的伺服器篩選代碼，退回掃描全部活動。")
        candidates = search_all_activities(open_signup_only=True)

    if max_candidates is not None:
        candidates = candidates[:max_candidates]

    print(
        f"[Activity Tools] 標籤「{tag_name}」從第 {start_index} 筆開始掃描"
        f"（候選活動共 {len(candidates)} 個{'（伺服器已篩選）' if tag_filter else ''}，"
        f"找滿 {limit if limit is not None else '不限'} 筆就停止）..."
    )

    items: list[dict[str, Any]] = []
    index = start_index

    while index < len(candidates):
        if limit is not None and len(items) >= limit:
            break

        activity = candidates[index]
        index += 1

        try:
            detail = get_activity_detail(activity["activity_id"])
        except Exception as e:
            print(f"[Activity Tools] 查詢活動 {activity['activity_id']} 詳情失敗：{e}")
            continue

        for sess in detail.get("sessions", []):
            tag = sess.get("passport_hours_tag") or ""
            if not tag or "不提供時數" in tag or tag_name not in tag:
                continue

            items.append(
                {
                    "activity_id": activity["activity_id"],
                    "activity_title": detail.get("title"),
                    "session_id": sess.get("session_id"),
                    "session_name": sess.get("session_name"),
                    "tag": tag,
                    "event_period": sess.get("event_period"),
                    "signup_period": sess.get("signup_period"),
                    "capacity": sess.get("capacity"),
                    "waitlist_capacity": sess.get("waitlist_capacity"),
                    "signup_status_text": sess.get("signup_status_text"),
                    "url": detail.get("url"),
                }
            )

            if limit is not None and len(items) >= limit:
                break

    exhausted = index >= len(candidates)

    return items, index, exhausted


def _scan_open_activities_for_tags(
    tag_names: list[str],
    max_candidates: Optional[int] = None,
    limit_per_tag: Optional[int] = 5,
    start_indices: Optional[dict[str, int]] = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int], dict[str, bool]]:
    """依標籤名稱清單分別查詢，每個標籤各自獨立掃描（各自的候選清單、
    各自的進度游標）。recommend_activities_for_categories() 跟
    find_activities_by_hour_tag() 共用這個，差別只在要不要附推薦理由。

    Args:
        max_candidates: 可選的安全上限，預設 None 代表候選活動不特別截斷。
        limit_per_tag: 每個標籤最多收集幾筆就提早停止（預設 5）。
            傳 None 代表不限制，掃描到候選活動結束為止。
        start_indices: {標籤名稱: 上次停在候選清單第幾筆}，使用者說
            「繼續」時，把上次回傳的 next_indices 原封不動傳進來。

    Returns:
        (matches, next_indices, exhausted_by_tag)
        - matches: {標籤名稱: [符合的場次資訊, ...]}
        - next_indices: {標籤名稱: 下次要從候選清單第幾筆繼續掃描}，
          下次呼叫時原封不動傳給 start_indices 即可
        - exhausted_by_tag: {標籤名稱: 是否已經掃到該標籤候選清單最後一筆}。
          True 代表 matches[name] 就是全部符合的場次（沒有再多了）；
          False 代表是因為找滿 limit_per_tag 才提早停止，可能還有更多，
          呼叫端顯示筆數時不該講成「總共只有這些」。
    """

    start_indices = start_indices or {}

    matches: dict[str, list[dict[str, Any]]] = {}
    next_indices: dict[str, int] = {}
    exhausted_by_tag: dict[str, bool] = {}

    for name in tag_names:
        items, next_index, exhausted = _scan_activities_for_tag(
            name, max_candidates, limit_per_tag, start_indices.get(name, 0)
        )
        matches[name] = items
        next_indices[name] = next_index
        exhausted_by_tag[name] = exhausted

    return matches, next_indices, exhausted_by_tag


def recommend_activities_for_categories(
    deficiencies: list[dict[str, Any]],
    max_candidates: Optional[int] = None,
    limit_per_tag: Optional[int] = 5,
    start_indices: Optional[dict[str, int]] = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int], dict[str, bool]]:
    """依「學習護照時數缺口」，從目前開放報名中的活動找出對應場次。

    Args:
        deficiencies: 每筆為 {"group": 大類別, "subcategory": 細項名稱,
            "confirmed_hours": 已核發, "required": 門檻, "remaining": 還差多少}，
            通常直接拿 action_tools.get_deficiency_details() 的回傳值。
            細項名稱要跟 action_tools.py 裡
            STUDY_PASSPORT_SUBCATEGORY_REQUIREMENTS 的細項名稱一致
            （例如 "校外服務"、"人文藝術"、"國際視野"）。
        max_candidates: 預設 None，候選活動不特別截斷。
        limit_per_tag: 每個細項最多找幾筆就提早停止（預設 5）。
            傳 None 代表掃描全部候選活動、不限制筆數。
        start_indices: {細項名稱: 上次停在候選清單第幾筆}，使用者說
            「繼續」時，把上次回傳的 next_indices 傳進來，
            接續掃描剩下的活動。

    Returns:
        (matches, next_indices, exhausted_by_tag)——matches 是
        {細項名稱: [符合的場次資訊（含 reason 推薦理由、報名人數/名額）, ...]}，
        next_indices/exhausted_by_tag 的意義見 _scan_open_activities_for_tags()。
    """

    subcategory_names = [d["subcategory"] for d in deficiencies]
    reason_by_name = {
        d["subcategory"]: (
            f"「{d['group']}」類別的「{d['subcategory']}」還差 "
            f"{d['remaining']} 小時（目前 {d['confirmed_hours']}/{d['required']}）"
        )
        for d in deficiencies
    }

    matches, next_indices, exhausted_by_tag = _scan_open_activities_for_tags(
        subcategory_names, max_candidates, limit_per_tag, start_indices
    )

    for name, items in matches.items():
        for item in items:
            item["reason"] = f"推薦原因：{reason_by_name[name]}"

    found_summary = ", ".join(f"{name}：{len(items)} 場" for name, items in matches.items())
    print(f"[Activity Tools] 推薦結果 - {found_summary}")

    return matches, next_indices, exhausted_by_tag


def find_activities_by_hour_tag(
    tag_names: list[str],
    max_candidates: Optional[int] = None,
    limit_per_tag: Optional[int] = 5,
    start_indices: Optional[dict[str, int]] = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int], dict[str, bool]]:
    """直接依「學習護照時數標籤」名稱查詢活動，不需要登入、也不需要知道
    使用者自己的時數狀況（跟 recommend_activities_for_categories 的差別：
    這個是使用者自己指名想找哪個類別，不是系統依缺口主動推薦）。

    例如：使用者問「有沒有自我探索與生涯規劃時數的活動」，
    直接呼叫 find_activities_by_hour_tag(["自我探索與生涯規劃"])。

    limit_per_tag 預設 5，找滿就提早停止（傳 None 代表掃描全部候選活動）；
    start_indices 用於使用者說「繼續/還要更多」時接續上次的掃描位置。

    Returns:
        (matches, next_indices, exhausted_by_tag)——matches 是
        {標籤名稱: [符合的場次資訊（含報名人數/名額）, ...]}，
        next_indices/exhausted_by_tag 的意義見 _scan_open_activities_for_tags()。
    """

    matches, next_indices, exhausted_by_tag = _scan_open_activities_for_tags(
        tag_names, max_candidates, limit_per_tag, start_indices
    )

    for name, items in matches.items():
        for item in items:
            item["reason"] = f"提供「{name}」時數"

    found_summary = ", ".join(f"{name}：{len(items)} 場" for name, items in matches.items())
    print(f"[Activity Tools] 查詢結果 - {found_summary}")

    return matches, next_indices, exhausted_by_tag


def format_activity_summary(detail: dict[str, Any]) -> str:
    """把 get_activity_detail() 的結果整理成一段人看得懂的活動摘要文字。

    設計目的：使用者（或 agent）在決定要不要報名一個活動之前，
    應該要能看到「這個活動在幹嘛、什麼時候、在哪裡、有沒有時數、
    是線上報名還是要現場報名」，而不是只看到一個活動名稱就被要求報名。
    """

    lines = [f"【{detail.get('title', '未知活動')}】"]

    if detail.get("department"):
        lines.append(f"承辦單位：{detail['department']}")
    if detail.get("contact_person"):
        contact = detail["contact_person"]
        if detail.get("contact_email"):
            contact += f"（{detail['contact_email']}）"
        lines.append(f"承辦人：{contact}")

    if detail.get("description"):
        lines.append(f"\n活動內容：\n{detail['description']}")

    sessions = detail.get("sessions", [])
    lines.append(f"\n共 {len(sessions)} 個場次：")

    for session in sessions:
        mode = session.get("registration_mode")
        mode_text = {
            "online": "線上報名",
            "onsite": "⚠️ 現場報名（系統上的報名時間起訖相同，代表無法事先線上報名）",
            "unknown": "報名方式不明，請查看活動內容說明",
        }.get(mode, "報名方式不明")

        lines.append(f"\n  ● {session.get('session_name', '（未命名場次）')}")
        if session.get("instructor"):
            lines.append(f"    講師/負責單位：{session['instructor']}")
        if session.get("location"):
            lines.append(f"    地點：{session['location']}")
        if session.get("event_period"):
            lines.append(f"    活動時間：{session['event_period']}")
        if session.get("signup_period"):
            lines.append(f"    報名時間：{session['signup_period']}")
        lines.append(f"    報名方式：{mode_text}")
        if session.get("signup_status_text"):
            lines.append(f"    {session['signup_status_text']}")

        hour_tags = [
            (label, session.get(field))
            for field, label in [
                ("hours_tag", "一般時數"),
                ("passport_hours_tag", "學習護照時數"),
                ("soft_skill_hours_tag", "軟實力時數"),
            ]
            if session.get(field) and "不提供時數" not in session.get(field, "")
        ]
        if hour_tags:
            lines.append(
                "    可獲得時數：" + "；".join(f"{label} {tag}" for label, tag in hour_tags)
            )

    return "\n".join(lines)


if __name__ == "__main__":
    # 簡單手動測試：搜尋 + 看第一筆的詳情
    found = search_activities(keyword="糖霜")
    for item in found:
        print(item)

    if found:
        detail = get_activity_detail(found[0]["activity_id"])
        print("\n=== 詳情 ===")
        print(detail)
