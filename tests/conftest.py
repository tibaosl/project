import pytest

from backend import usage_log
from backend.rag import answer_cache


@pytest.fixture(autouse=True)
def _usage_log_in_temp_dir(tmp_path_factory, monkeypatch):
    """測試裡用假回應呼叫到的用量紀錄寫到暫存資料夾，不要混進 storage/usage/ 的真實紀錄。"""
    monkeypatch.setattr(usage_log, "LOG_DIR", tmp_path_factory.mktemp("usage"))


@pytest.fixture(autouse=True)
def _answer_cache_in_temp_dir(tmp_path_factory, monkeypatch):
    """法規問答的回答快取也寫到暫存資料夾，每個測試一個新的（不會用到別的測試存的回答）。
    執行測試的環境變數關掉快取時也照樣打開，測的是程式本身。"""
    monkeypatch.setattr(answer_cache, "CACHE_DIR", tmp_path_factory.mktemp("answers"))
    monkeypatch.setattr(answer_cache, "ENABLED", True)
    monkeypatch.setattr(answer_cache, "_state", {"catalog": None, "state": None})
    monkeypatch.setattr(answer_cache, "_version_cache", {})
