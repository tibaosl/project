import streamlit as st
import requests
import urllib.parse
import ast
import json

st.set_page_config(page_title="NCUXplore 校園助手", page_icon="🎓")
st.title("NCUXplore 智慧校園代理系統")
st.caption("歡迎使用！我可以幫你查詢校園法規、時數進度，還有活動查詢與報名喔！")

st.sidebar.header("Portal 登入設定")
st.sidebar.caption("若要請 AI 幫忙登入 Portal，請先在此輸入帳密：")
user_id = st.sidebar.text_input("帳號")
user_pwd = st.sidebar.text_input("密碼", type="password")
st.sidebar.warning("僅供本次測試使用，重整網頁後即會清除。")

# ------------------------------------------------------------------
# 共用卡片樣式（時數進度、活動推薦、活動詳情都會用到）。
# ------------------------------------------------------------------
st.markdown(
    """
    <style>
        .ncux-card {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            padding: 14px 16px;
            margin: 10px 0;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
        }
        .ncux-card-title {
            font-size: 15px;
            font-weight: 700;
            color: #0f172a;
            margin-bottom: 4px;
        }
        .ncux-card-meta {
            font-size: 12.5px;
            color: #64748b;
            margin-bottom: 2px;
        }
        .ncux-badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 999px;
            font-size: 11.5px;
            font-weight: 600;
            margin-right: 6px;
        }
        .ncux-badge-ok { background: #dcfce7; color: #166534; }
        .ncux-badge-warn { background: #ffedd5; color: #9a3412; }
        .ncux-badge-info { background: #dbeafe; color: #1e40af; }
        .ncux-reason {
            font-size: 12.5px;
            color: #7c3aed;
            margin-top: 4px;
        }
        .ncux-banner {
            border-radius: 10px;
            padding: 12px 16px;
            font-weight: 600;
            margin-bottom: 10px;
        }
        .ncux-banner-ok { background: #dcfce7; color: #166534; }
        .ncux-banner-warn { background: #fef3c7; color: #92400e; }
        .ncux-confirm-box {
            background: #fff7ed;
            border: 1px solid #fdba74;
            border-radius: 10px;
            padding: 12px 16px;
            margin-top: 12px;
            font-weight: 600;
            color: #9a3412;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


def format_course_cell(details):
    if not details:
        return ""
    courses_html = []
    for i in range(0, len(details), 3):
        chunk = details[i : i + 3]
        if len(chunk) == 3:
            code, title, loc = chunk[0], chunk[1], chunk[2]
            c_html = f"""<div class='course-block'>
                <div class='course-code'>{code}</div>
                <div class='course-title'>{title}</div>
                <div class='course-location'>{loc}</div>
            </div>"""
        elif len(chunk) == 2:
            c_html = f"""<div class='course-block'>
                <div class='course-title'>{chunk[0]}</div>
                <div class='course-location'>{chunk[1]}</div>
            </div>"""
        else:
            c_html = f"""<div class='course-block'>
                <div class='course-title'>{chunk[0]}</div>
            </div>"""
        courses_html.append(c_html)

    return "<div class='course-divider'></div>".join(courses_html)


def render_hours_dashboard(data):
    """渲染時數 dashboard：整體達標狀態 + 每個大類別/細項的進度條。"""

    graduated = data.get("graduated")
    banner_class = "ncux-banner-ok" if graduated else "ncux-banner-warn"
    banner_text = "✅ 學習護照時數已達畢業門檻！" if graduated else "⚠️ 學習護照時數尚未達畢業門檻"
    st.markdown(f"<div class='ncux-banner {banner_class}'>{banner_text}</div>", unsafe_allow_html=True)

    for group, cat in data.get("categories", {}).items():
        status_ok = cat.get("passed_graduation")
        badge_class = "ncux-badge-ok" if status_ok else "ncux-badge-warn"
        badge_text = "已達標" if status_ok else "尚未達標"

        # 用有背景色的卡片包住標題列，不要把深色文字直接放在頁面背景上——
        # Streamlit 深色主題下會整段看不見。
        st.markdown(
            f"""
            <div class="ncux-card" style="padding: 8px 16px; margin-bottom: 4px;">
                <span class="ncux-card-title">{group}</span>
                <span class="ncux-badge {badge_class}">{badge_text}</span>
                <span class="ncux-card-meta">門檻總計 {cat.get('graduation_required')} 小時</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        for sub_name, sub in cat.get("subcategories", {}).items():
            required = sub.get("required") or 0
            confirmed = sub.get("confirmed_hours") or 0
            ratio = min(1.0, confirmed / required) if required else 1.0

            sub_icon = "✅" if sub.get("passed") else "⚠️"
            caption = f"{sub_icon} {sub_name}：{confirmed}/{required} 小時"
            if not sub.get("passed"):
                caption += f"（還差 {sub.get('remaining')} 小時）"
            if sub.get("pending_hours"):
                caption += f"，另有 {sub['pending_hours']} 小時待核發"

            st.progress(ratio, text=caption)

        st.markdown("<div style='margin-bottom: 8px;'></div>", unsafe_allow_html=True)


def render_activity_recommendations(data):
    """渲染活動清單（依時數缺口推薦、或依標籤直接查詢都用這個），
    每張卡片附上推薦/查詢理由，以及報名人數/名額——
    使用者選活動最在意的就是還有沒有名額，不能只給活動資訊不給名額，
    等到真的要報名才發現額滿。
    """

    recommendations = data.get("recommendations", {})
    any_found = any(items for items in recommendations.values())

    if not any_found:
        st.info("目前開放報名中的活動裡沒有找到符合的場次，之後可以再查一次。")
        return

    for name, items in recommendations.items():
        if not items:
            continue

        # 後端（activity_tools）已經依 limit_per_tag 提早停止掃描，
        # 這裡顯示的就是實際找到的筆數，不再另外截斷——
        # 避免「標題寫找到 8 個，卡片卻只顯示 5 個」這種不一致。
        st.markdown(f"**【{name}】找到 {len(items)} 個場次**")

        for item in items:
            headcount = item.get("signup_status_text")
            if not headcount and item.get("capacity") is not None:
                headcount = f"名額上限：{item['capacity']}"
            headcount_line = (
                f"<div class='ncux-card-meta'>👥 {headcount}</div>" if headcount else ""
            )

            st.markdown(
                f"""
                <div class="ncux-card">
                    <div class="ncux-card-title">{item.get('activity_title', '')}</div>
                    <div class="ncux-card-meta">場次：{item.get('session_name', '')}</div>
                    <div class="ncux-card-meta">🕒 活動時間：{item.get('event_period', '')}</div>
                    <div class="ncux-card-meta">📝 報名時間：{item.get('signup_period', '')}</div>
                    <div class="ncux-card-meta">🎖️ 時數標籤：{item.get('tag', '')}</div>
                    {headcount_line}
                    <div class="ncux-reason">💡 {item.get('reason', '')}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    if data.get("has_more"):
        st.caption("還有更多符合的活動——回覆「繼續」再看幾個，或「全部列出」看完整清單。")


def render_activity_card(session):
    """渲染單一場次的資訊卡片（活動詳情、報名確認都會用到）。"""

    mode = session.get("registration_mode")
    mode_badge = {
        "online": ("線上報名", "ncux-badge-ok"),
        "onsite": ("⚠️ 現場報名", "ncux-badge-warn"),
    }.get(mode, ("報名方式不明", "ncux-badge-info"))

    hour_tags = [
        (label, session.get(field))
        for field, label in [
            ("passport_hours_tag", "學習護照時數"),
            ("soft_skill_hours_tag", "軟實力時數"),
        ]
        if session.get(field) and "不提供時數" not in session.get(field, "")
    ]
    hour_line = (
        "".join(f"<div class='ncux-card-meta'>🎖️ {label}：{tag}</div>" for label, tag in hour_tags)
        if hour_tags
        else ""
    )

    st.markdown(
        f"""
        <div class="ncux-card">
            <div class="ncux-card-title">{session.get('session_name', '（未命名場次）')}
                <span class="ncux-badge {mode_badge[1]}">{mode_badge[0]}</span>
            </div>
            <div class="ncux-card-meta">👤 {session.get('instructor', '')}</div>
            <div class="ncux-card-meta">📍 地點：{session.get('location', '')}</div>
            <div class="ncux-card-meta">🕒 活動時間：{session.get('event_period', '')}</div>
            <div class="ncux-card-meta">📝 報名時間：{session.get('signup_period', '')}</div>
            {hour_line}
            <div class="ncux-card-meta">{session.get('signup_status_text', '')}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_activity_detail(data, confirmation_label=None):
    """渲染活動詳情（也用於報名/取消報名前的確認畫面）。"""

    st.markdown(f"### {data.get('title', '未知活動')}")

    meta_bits = []
    if data.get("department"):
        meta_bits.append(f"承辦單位：{data['department']}")
    if data.get("contact_person"):
        contact = data["contact_person"]
        if data.get("contact_email"):
            contact += f"（{data['contact_email']}）"
        meta_bits.append(f"承辦人：{contact}")
    if meta_bits:
        st.caption(" ｜ ".join(meta_bits))

    for session in data.get("sessions", []):
        render_activity_card(session)

    if confirmation_label:
        st.markdown(
            f"""
            <div class="ncux-confirm-box">
                ⚠️ 確定要{confirmation_label}這個活動嗎？這個動作會真的改變你在學校系統上的報名紀錄，
                請回覆「確定{confirmation_label}」來送出。
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_agent_reply(content):
    """判斷並渲染文字或結構化的課表資料"""
    if isinstance(content, str):
        content_str = content.strip()
        if content_str.startswith("[") and content_str.endswith("]"):
            try:
                parsed = ast.literal_eval(content_str)
                if isinstance(parsed, list):
                    content = parsed
            except Exception:
                try:
                    parsed = json.loads(content_str)
                    if isinstance(parsed, list):
                        content = parsed
                except Exception:
                    pass

    if isinstance(content, dict) and content.get("kind"):
        kind = content["kind"]
        if kind == "hours_dashboard":
            render_hours_dashboard(content)
            return
        if kind in ("activity_recommendations", "activity_tag_search"):
            render_activity_recommendations(content)
            return
        if kind == "activity_detail":
            render_activity_detail(content)
            return
        if kind == "activity_confirmation":
            render_activity_detail(content, confirmation_label=content.get("action_label"))
            return
        # 沒對到已知的 kind，降級成印出原始資料方便除錯
        st.write(content)
        return

    while (
        isinstance(content, list)
        and len(content) > 0
        and isinstance(content[0], list)
    ):
        content = content[0]

    if isinstance(content, list) and len(content) > 0 and isinstance(content[0], dict):
        st.markdown("### 📅 個人本學期課表總覽")
        
        try:
            periods_info = [
                ("第一節", "08:00-08:50"),
                ("第二節", "09:00-09:50"),
                ("第三節", "10:00-10:50"),
                ("第四節", "11:00-11:50"),
                ("中午",   "12:00-12:50"),
                ("第五節", "13:00-13:50"),
                ("第六節", "14:00-14:50"),
                ("第七節", "15:00-15:50"),
                ("第八節", "16:00-16:50"),
                ("第九節", "17:00-17:50"),
                ("第Ａ節", "18:00-18:50"),
                ("第Ｂ節", "19:00-19:50"),
                ("第Ｃ節", "20:00-20:50")
            ]
            days = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六"]
            grid = {}
            for item in content:
                p = str(item.get("period", "")).replace("第A節", "第Ａ節").replace("第B節", "第Ｂ節").replace("第C節", "第Ｃ節")
                d = str(item.get("day", "")).strip()
                details = item.get("details", [])
                if p and d:
                    if (p, d) not in grid:
                        grid[(p, d)] = []
                    grid[(p, d)].extend(details)

            css_style = """
            <style>
                .timetable-card-container {
                    width: 100%;
                    overflow-x: auto;
                    margin: 15px 0;
                }
                .custom-timetable {
                    width: 100%;
                    table-layout: fixed;
                    border-collapse: separate;
                    border-spacing: 6px;
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                }
                .custom-timetable th {
                    background: #1e40af;
                    color: #ffffff;
                    padding: 10px 4px;
                    border-radius: 6px;
                    font-size: 14px;
                    font-weight: 600;
                    text-align: center;
                }
                .custom-timetable td {
                    background: #ffffff;
                    border: 1px solid #e2e8f0;
                    border-radius: 8px;
                    padding: 8px 6px;
                    vertical-align: top;
                    min-height: 70px;
                    text-align: center;
                    word-break: break-word;
                }
                .time-header-cell {
                    background: #f8fafc !important;
                    border: 1px solid #cbd5e1 !important;
                    font-weight: bold;
                    color: #334155;
                    text-align: center !important;
                    width: 100px;
                }
                /* 有課的儲存格卡片：左側加上天藍色邊條高亮 */
                .custom-timetable td.has-course {
                    background: #f0f9ff;
                    border: 1px solid #bae6fd;
                    border-left: 4px solid #0284c7;
                    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
                }
                .custom-timetable td.has-course:hover {
                    transform: translateY(-2px);
                    box-shadow: 0 4px 12px rgba(2, 132, 199, 0.15);
                    border-color: #38bdf8;
                }
                .course-block {
                    padding: 2px 0;
                }
                .course-code {
                    color: #64748b;
                    font-size: 11px;
                    font-weight: 500;
                    margin-bottom: 2px;
                }
                .course-title {
                    color: #0f172a;
                    font-size: 13px;
                    font-weight: 700;
                    line-height: 1.35;
                    margin-bottom: 3px;
                }
                .course-location {
                    color: #0284c7;
                    font-size: 11px;
                    font-weight: 600;
                }
                .course-divider {
                    margin: 6px 0;
                    border-top: 1px dashed #cbd5e1;
                }
            </style>
            """

            html = [css_style, "<div class='timetable-card-container'><table class='custom-timetable'>"]
            html.append("<thead><tr><th style='width: 100px;'>節次 / 時間</th>")
            for day in days:
                html.append(f"<th>{day}</th>")
            html.append("</tr></thead><tbody>")

            for p_name, p_time in periods_info:
                has_any_class = any((p_name, d) in grid for d in days)
                if not has_any_class and p_name in ["中午", "第九節", "第Ａ節", "第Ｂ節", "第Ｃ節"]:
                    continue

                html.append(f"<tr><td class='time-header-cell'>{p_name}<br><span style='font-size:10px; color:#64748b; font-weight:normal;'>{p_time}</span></td>")
                
                for d in days:
                    details = grid.get((p_name, d), None)
                    if details:
                        cell_content_html = format_course_cell(details)
                        html.append(
                            f"<td class='has-course'>{cell_content_html}</td>"
                        )
                    else:
                        html.append("<td></td>")
                html.append("</tr>")

            html.append("</tbody></table></div>")
            st.markdown("".join(html), unsafe_allow_html=True)

        except Exception as e:
            st.warning(f"課表繪製發生小錯誤（{e}），降級顯示原始列表：")
            st.write(content)

    elif isinstance(content, str):
        st.markdown(content)

    else:
        st.write(content)

if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": "你好！我是 NCUXplore，今天想查點什麼？"}]

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        render_agent_reply(msg["content"])
        
        if msg.get("sources"):
            with st.expander("📚 參考資料來源", expanded=False):
                for src in msg["sources"]:
                    file_name = src.split(" (")[0] 
                    safe_url = urllib.parse.quote(file_name)
                    st.markdown(f"• 📄 <a href='http://127.0.0.1:8000/files/{safe_url}' target='_blank'>{src}</a>", unsafe_allow_html=True)


if prompt := st.chat_input("請輸入你的問題 (例如：請幫我查詢本學期課表)..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Agent 正在思考與執行中，請稍候..."):
            try:
                payload = {
                    "user_message": prompt,
                    "username": user_id,
                    "password": user_pwd
                }
                
                response = requests.post("http://127.0.0.1:8000/api/chat", json=payload, timeout=120)
                
                if response.status_code == 200:
                    response_data = response.json()
                    results = response_data.get("response", [])
                    sources = response_data.get("sources", [])

                    if results:
                        bot_reply = results[0] 
                    else:
                        bot_reply = "後端回傳結果為空。"

                else:
                    bot_reply = f"系統發生錯誤，狀態碼：{response.status_code}\n詳細錯誤：{response.text}"
                    sources = []
                    
            except Exception as e:
                bot_reply = f"發生未預期錯誤：{str(e)}"
                sources = []

            render_agent_reply(bot_reply)

            if sources:
                with st.expander("📚 參考資料來源", expanded=True):
                    for src in sources:
                        file_name = src.split(" (")[0] 
                        safe_url = urllib.parse.quote(file_name)
                        st.markdown(f"• 📄 <a href='http://127.0.0.1:8000/files/{safe_url}' target='_blank'>{src}</a>", unsafe_allow_html=True)

        st.session_state.messages.append({"role": "assistant", "content": bot_reply, "sources": sources})