"""集中設定 logging，並提供一個呼叫方式跟內建 print() 相容的小工具。

背景：專案裡原本大量直接用 print() 輸出除錯訊息（100+ 處，很多是多個位置
參數的呼叫，例如 print("URL:", page.url)）。如果逐一手動把每個 print()
改名成 logger.info(...)，多參數的那些會在執行時丟例外——logging 的
Logger.info(msg, *args) 會把 msg 後面的參數當成 %-style 格式化參數，
msg 裡沒有對應的 %s 就會丟 TypeError，而且要一路等到真的被印出來才會炸開。

所以這裡改用 make_print_logger()：回傳的函式呼叫方式跟 print() 完全一樣
（吃任意數量的位置參數、支援 sep=/end=），只是實際輸出走 logging——
可以用環境變數 LOG_LEVEL 統一控制詳細程度、之後也能輕鬆改成輸出到檔案，
但完全不用去動任何一個既有的 print() 呼叫點。
"""

import logging
import os

_configured = False


def setup_logging() -> None:
    """設定全域 logging（idempotent，重複呼叫沒有副作用）。"""
    global _configured
    if _configured:
        return

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    _configured = True


def make_print_logger(module_name: str):
    """回傳一個跟 print() 呼叫方式相容、但實際透過 logging 輸出的函式。

    用法：在模組頂端 `print = make_print_logger(__name__)`，該模組裡所有
    既有的 print(...) 呼叫都會自動改走 logging，不需要逐一修改。
    """
    setup_logging()
    logger = logging.getLogger(module_name)

    def _print(*args, sep: str = " ", end: str = "\n", **kwargs) -> None:
        logger.info(sep.join(str(a) for a in args))

    return _print
