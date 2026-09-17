"""中央大學 Portal 官方 OAuth 2.0 介接（見 https://portal.ncu.edu.tw/about/howto）。

用「Authorization Code」流程確認使用者身分：使用者被導去 portal.ncu.edu.tw
自己的頁面輸入帳密，我們的伺服器全程看不到密碼，只會拿到一個 access token，
再用這個 token 換使用者的 identifier（帳號）/中文姓名。

⚠️ 這個官方 API 只提供「身分驗證」，沒有課表/時數/選課相關的資料或操作
介面——`action_tools.py` 用 Playwright 模擬瀏覽器操作那些功能，目前沒有
官方 API 可以取代，OAuth 驗證完身分後，要用那些功能還是得另外走一次
`/api/login`（帳密）才能建立 Playwright session，見 main.py 的
`/api/oauth/callback` 與 `/api/login` 之間的分工說明。
"""

import asyncio
import os
import secrets
import time
from typing import Any

import requests

from logging_config import make_print_logger

print = make_print_logger(__name__)

CLIENT_ID = os.environ.get("NCU_OAUTH_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("NCU_OAUTH_CLIENT_SECRET", "")

# 開發環境預設值：後端固定跑在 127.0.0.1:8000（見 main.py 最下面
# uvicorn.run 的設定），這裡的預設值要跟電算中心申請 client_id 時
# 登記的 redirect_uri 完全一致（OAuth 規範要求逐字比對），不一致就
# 只能在 .env 覆寫 NCU_OAUTH_REDIRECT_URI。
REDIRECT_URI = os.environ.get("NCU_OAUTH_REDIRECT_URI", "http://127.0.0.1:8000/api/oauth/callback")

# OAuth 流程結束後（不管成功或失敗）要把瀏覽器導回前端的哪個網址。
# 開發時前端是另一個 port 的 Vite dev server；正式環境 main.py 會把
# build 好的前端一起 serve，這時可以把這個設成空字串或跟後端同源的
# 相對路徑（見下面 build_return_url 的用法）。
FRONTEND_ORIGIN = os.environ.get("NCU_OAUTH_FRONTEND_ORIGIN", "http://localhost:5173")

AUTHORIZATION_URL = "https://portal.ncu.edu.tw/oauth2/authorization"
TOKEN_URL = "https://portal.ncu.edu.tw/oauth2/token"
USERINFO_URL = "https://portal.ncu.edu.tw/apis/oauth/v1/info"

# 我們只需要帳號（拿來當 app 內部的 username，串接既有的 session/token 機制）
# 跟中文姓名（畫面顯示用），不需要學號、身分證字號這些更敏感的資訊。
SCOPES = "identifier chinese-name"

# state -> 建立時間戳，用來防 CSRF、順便擋掉重放攻擊；純記憶體，10 分鐘
# 沒用到就視為過期（正常登入流程走完一輪也就幾秒鐘，10 分鐘非常寬鬆）。
_pending_states: dict[str, float] = {}
_STATE_TTL_SECONDS = 600


def _prune_expired_states():
    now = time.time()
    expired = [s for s, created in _pending_states.items() if now - created > _STATE_TTL_SECONDS]
    for s in expired:
        _pending_states.pop(s, None)


def is_configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET)


def build_authorization_url() -> str:
    """組出讓瀏覽器導去的 Portal OAuth 授權網址，內含一次性 state。"""
    if not is_configured():
        raise RuntimeError(
            "尚未設定 NCU_OAUTH_CLIENT_ID / NCU_OAUTH_CLIENT_SECRET，"
            "請先把電算中心核發的值加進 .env。"
        )

    _prune_expired_states()
    state = secrets.token_urlsafe(24)
    _pending_states[state] = time.time()

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
    }
    query = "&".join(f"{k}={requests.utils.quote(v, safe='')}" for k, v in params.items())
    return f"{AUTHORIZATION_URL}?{query}"


def consume_state(state: str) -> bool:
    """驗證 callback 帶回來的 state 是不是我們發出去的那個，驗證完就直接消費掉
    （不能重複使用同一個 state）。"""
    _prune_expired_states()
    if not state or state not in _pending_states:
        return False
    _pending_states.pop(state, None)
    return True


def _exchange_code_sync(code: str) -> dict[str, Any]:
    """同步版本（實際打 HTTP），供 exchange_code_for_identity 包在 thread 裡跑，
    避免擋住 FastAPI 的 event loop。"""
    token_res = requests.post(
        TOKEN_URL,
        auth=(CLIENT_ID, CLIENT_SECRET),
        headers={"Accept": "application/json"},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        timeout=15,
    )
    token_res.raise_for_status()
    access_token = token_res.json().get("access_token")
    if not access_token:
        raise RuntimeError(f"Portal OAuth token 回應裡沒有 access_token：{token_res.text[:500]}")

    userinfo_res = requests.get(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    userinfo_res.raise_for_status()
    userinfo = userinfo_res.json()

    identifier = userinfo.get("identifier")
    if not identifier:
        raise RuntimeError(f"Portal OAuth 使用者資訊裡沒有 identifier（帳號）：{userinfo}")

    return {
        "identifier": identifier,
        "chinese_name": userinfo.get("chineseName") or userinfo.get("chinese-name") or "",
    }


async def exchange_code_for_identity(code: str) -> dict[str, Any]:
    """用 authorization code 換 access token，再換使用者身分（identifier/中文姓名）。

    整段都是一般同步 HTTP 呼叫（`requests`，跟專案其他地方一致，沒有另外
    引入非同步 HTTP client），丟進 thread 跑，不要卡住 FastAPI 的 event loop。
    """
    return await asyncio.to_thread(_exchange_code_sync, code)


def build_return_url(path: str, **query: str) -> str:
    """組出 OAuth 流程結束後要導回前端的網址（成功會帶 token/username，
    失敗會帶 oauth_error）。"""
    qs = "&".join(f"{k}={requests.utils.quote(v, safe='')}" for k, v in query.items() if v)
    base = f"{FRONTEND_ORIGIN}{path}"
    return f"{base}?{qs}" if qs else base
