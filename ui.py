import streamlit as st
import requests

st.set_page_config(page_title="NCUXplore 校園助手", page_icon="🎓")
st.title("NCUXplore 智慧校園代理系統")
st.caption("歡迎使用！我可以幫你查詢校園法規，或是自動幫你登入 Portal 喔！")

st.sidebar.header("Portal 登入設定")
st.sidebar.caption("若要請 AI 幫忙登入 Portal，請先在此輸入帳密：")
user_id = st.sidebar.text_input("帳號")
user_pwd = st.sidebar.text_input("密碼", type="password")
st.sidebar.warning("僅供本次測試使用，重整網頁後即會清除。")

if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": "你好！我是 NCUXplore，今天想查點什麼？"}]

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("請輸入你的問題，例如：幫我登入系統 / 查一下資工系教室借用規定。"):
    
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("AI 大腦思考中，請稍候..."):
            try:
                api_url = "http://127.0.0.1:8000/api/chat" 
                payload = {"user_message": prompt, "username": user_id, "password": user_pwd}
                response = requests.post(api_url, json=payload, timeout=30)
                
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("response", [])
                    
                    if isinstance(results, list):
                        valid_results = [str(item) for item in results if str(item).strip()]
                        if valid_results:
                            bot_reply = "\n\n".join(valid_results)
                        else:
                            bot_reply = "後端已處理完畢，但未傳回有效的文字結果。"
                    elif results:
                        bot_reply = str(results)
                    else:
                        bot_reply = "後端回傳結果為空。"

                else:
                    bot_reply = f"系統發生錯誤，狀態碼：{response.status_code}\n詳細錯誤：{response.text}"
                    
            except requests.exceptions.ConnectionError:
                bot_reply = "無法連線到後端！請確認你的 FastAPI (main.py) 有啟動喔！"
            except requests.exceptions.Timeout:
                bot_reply = "請求逾時！後端處理時間太長，請再試一次。"
            except Exception as e:
                bot_reply = f"發生未預期錯誤：{str(e)}"

            st.markdown(bot_reply)
            st.session_state.messages.append({"role": "assistant", "content": bot_reply})