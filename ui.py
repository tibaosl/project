import streamlit as st
import requests
import urllib.parse
import ast
import json

st.set_page_config(page_title="NCUXplore 校園助手", page_icon="🎓")
st.title("NCUXplore 智慧校園代理系統")
st.caption("歡迎使用！我可以幫你查詢校園法規，或是自動幫你登入 Portal 喔！")

st.sidebar.header("Portal 登入設定")
st.sidebar.caption("若要請 AI 幫忙登入 Portal，請先在此輸入帳密：")
user_id = st.sidebar.text_input("帳號")
user_pwd = st.sidebar.text_input("密碼", type="password")
st.sidebar.warning("僅供本次測試使用，重整網頁後即會清除。")

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