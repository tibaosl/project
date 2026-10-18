import pytest

from backend import usage_log


@pytest.fixture(autouse=True)
def _usage_log_in_temp_dir(tmp_path_factory, monkeypatch):
    """測試裡用假回應呼叫到的用量紀錄寫到暫存資料夾，不要混進 storage/usage/ 的真實紀錄。"""
    monkeypatch.setattr(usage_log, "LOG_DIR", tmp_path_factory.mktemp("usage"))
