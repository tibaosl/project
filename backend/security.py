"""本機 API 的基本防護（main.py 掛上 LocalOnlyMiddleware）。

後端只聽 127.0.0.1，但使用者用瀏覽器開的任何網站，都可以對 localhost 發請求：

- DNS rebinding：惡意網域把自己解析成 127.0.0.1 之後，瀏覽器會把它當成同源，讀得到 API 的回應
  （例如 /api/login/chrome 回傳的 token）。所以只接受 Host 是本機的請求。
- CSRF：別的網站可以用表單或 fetch 偷偷 POST 到 /api/login/chrome，在這台電腦開 Chrome、
  靠 Portal 的「記住我」自動登入。瀏覽器發跨站請求一定會帶 Origin，不是本機的就拒絕。
  沒帶 Origin 的（curl、測試）照常處理，那些不是從瀏覽器的網頁發出來的。
- 一般的安全標頭：不准被別的網站用 iframe 嵌入（避免誘騙點擊）、不要猜檔案類型、不送 Referer。

開發時前端走 Vite 的 proxy（vite.config.js 設了 changeOrigin），Host 會變成 127.0.0.1:8000，
Origin 還是瀏覽器原本的 http://localhost:5173，兩個都是本機。
"""

from urllib.parse import urlsplit

LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
    (b"referrer-policy", b"no-referrer"),
)


def host_is_local(host: str) -> bool:
    """Host 標頭（可能帶 port，IPv6 有中括號）是不是本機。"""
    try:
        hostname = urlsplit(f"//{host.strip()}").hostname
    except ValueError:
        return False
    return hostname in LOCAL_HOSTNAMES


def origin_is_local(origin: str) -> bool:
    """Origin 標頭是不是本機的 http(s) 網頁。沙盒 iframe、本機檔案送的是字串 "null"，不算。"""
    try:
        parts = urlsplit(origin.strip())
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and parts.hostname in LOCAL_HOSTNAMES


class LocalOnlyMiddleware:
    """純 ASGI 寫法，不會像 BaseHTTPMiddleware 那樣把 SSE 串流整段收起來才送。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {name.decode("latin-1").lower(): value.decode("latin-1") for name, value in scope["headers"]}
        if not host_is_local(headers.get("host", "")):
            await _reject(send, 400, "只接受來自本機的請求（Host 不是 localhost）。")
            return
        origin = headers.get("origin")
        if scope["method"] not in SAFE_METHODS and origin is not None and not origin_is_local(origin):
            await _reject(send, 403, "不接受其他網站送來的請求。")
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                existing = {name.lower() for name, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [
                    (name, value) for name, value in SECURITY_HEADERS if name not in existing
                ]
            await send(message)

        await self.app(scope, receive, send_with_headers)


async def _reject(send, status: int, message: str):
    body = message.encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode())]
                   + list(SECURITY_HEADERS),
    })
    await send({"type": "http.response.body", "body": body})
