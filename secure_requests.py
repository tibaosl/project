"""requests.get 的小包裝：預設驗證 TLS 憑證，只有真的驗證失敗才退回不驗證重試。

背景：中大大部分網域（csie.ncu.edu.tw、pdc.adm.ncu.edu.tw、lc.ncu.edu.tw、
portal.ncu.edu.tw...）憑證都正常，可以正常驗證；但 cis.ncu.edu.tw 的憑證鏈
缺了 Subject Key Identifier，用標準 TLS 驗證一定會失敗
（SSLCertVerificationError: Missing Subject Key Identifier），不是暫時性
問題，是對方網站的設定本身有缺陷。

與其整個專案打中大網域一律關掉憑證驗證（原本 crawler_tools.py /
activity_tools.py 的做法），這裡改成「先驗證，驗證失敗才退回不驗證」：
- 正常網域（例如爬蟲會爬到的大部分頁面）維持正常的憑證驗證。
- 只有 cis.ncu.edu.tw 這種已知有憑證缺陷的網域才會真的用不驗證的方式重試，
  而且每次都會印出明顯警告，不會被無聲關掉驗證、也不會在不小心打到別的
  有憑證問題的網域時毫無察覺。
"""

import requests
import urllib3

from logging_config import make_print_logger

print = make_print_logger(__name__)


def get_with_fallback(url: str, **kwargs) -> requests.Response:
    """等同 requests.get()，但只有在 TLS 憑證驗證失敗時才退回不驗證重試。"""
    try:
        return requests.get(url, verify=True, **kwargs)
    except requests.exceptions.SSLError as e:
        print(
            f"⚠️ {url} 的 TLS 憑證驗證失敗（{e}），改用不驗證憑證的方式重試一次。"
            "這代表對方網站的憑證設定有問題，或連線可能被中間人攔截——"
            "如果不是已知有憑證缺陷的網域（例如 cis.ncu.edu.tw），請不要習以為常，"
            "應該先確認網路環境是否安全。"
        )
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return requests.get(url, verify=False, **kwargs)
