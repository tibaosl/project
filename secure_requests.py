"""requests.get 的小包裝：預設驗證 TLS 憑證，驗證失敗才一步一步放寬。

背景：中大大部分網域（csie.ncu.edu.tw、pdc.adm.ncu.edu.tw、lc.ncu.edu.tw、
portal.ncu.edu.tw...）憑證都正常，可以正常驗證；但 cis.ncu.edu.tw 的憑證鏈
缺了 Subject Key Identifier，Python 3.13 起預設開啟的 X.509 嚴格檢查
（ssl.VERIFY_X509_STRICT）一定會擋下來（SSLCertVerificationError: Missing
Subject Key Identifier），不是暫時性問題，是對方網站的設定本身有缺陷。

與其整個專案打中大網域一律關掉憑證驗證（原本舊版爬蟲 /
activity_tools.py 的做法），這裡依序：
1. 正常驗證（正常網域都在這一步成功）。
2. 失敗的話，照樣驗證憑證鏈跟網域名稱，只是不套用嚴格檢查。cis.ncu.edu.tw 在這一步
   成功（2026-10 實測），之後同一個網域直接用這種連線，不用每次先失敗一次。
3. 還是失敗才不驗證重試，而且每次都印出明顯警告——到這一步代表憑證鏈或網域名稱
   真的對不上，不只是格式不夠嚴謹，可能是連線被中間人攔截。
"""

import ssl
from urllib.parse import urlparse

import requests
import urllib3
from requests.adapters import HTTPAdapter

from logging_config import make_print_logger

print = make_print_logger(__name__)


class NonStrictX509Adapter(HTTPAdapter):
    """照常驗證憑證鏈跟網域名稱，只是不套用 X.509 嚴格檢查（VERIFY_X509_STRICT）。"""

    def init_poolmanager(self, *args, **kwargs):
        context = ssl.create_default_context()
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        kwargs["ssl_context"] = context
        return super().init_poolmanager(*args, **kwargs)


_non_strict_session = requests.Session()
_non_strict_session.mount("https://", NonStrictX509Adapter())
# 已經知道過不了嚴格檢查、但一般驗證沒問題的網域
_non_strict_hosts: set[str] = set()


def get_with_fallback(url: str, **kwargs) -> requests.Response:
    """等同 requests.get()，但 TLS 憑證驗證失敗時依序放寬（見檔案開頭的說明）。"""
    host = urlparse(url).hostname or ""
    strict_error = None
    if host not in _non_strict_hosts:
        try:
            return requests.get(url, verify=True, **kwargs)
        except requests.exceptions.SSLError as e:
            strict_error = e

    try:
        response = _non_strict_session.get(url, **kwargs)
    except requests.exceptions.SSLError as e:
        print(
            f"⚠️ {url} 的 TLS 憑證驗證失敗（{e}），改用不驗證憑證的方式重試一次。"
            "憑證鏈或網域名稱對不上，連線可能被中間人攔截——請先確認網路環境是否安全。"
        )
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return requests.get(url, verify=False, **kwargs)

    if strict_error is not None:
        _non_strict_hosts.add(host)
        print(
            f"[secure_requests] {host} 的憑證過不了 X.509 嚴格檢查（{strict_error}），"
            "之後改用一般驗證（照樣檢查憑證鏈跟網域名稱）。"
        )
    return response
