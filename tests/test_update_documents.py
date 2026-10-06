"""update_documents.py 的流程測試，用假的步驟，不會真的爬網站或呼叫 API：
    python -m pytest tests/test_update_documents.py
"""

import sys

from backend.rag import update_documents as ud


def _use_steps(tmp_path, monkeypatch, steps):
    monkeypatch.setattr(ud, "LOG_DIR", tmp_path)
    monkeypatch.setattr(ud, "STEPS", steps)


def _log(tmp_path) -> str:
    return next(tmp_path.glob("*.log")).read_text(encoding="utf-8")


def test_every_step_runs_and_its_output_goes_to_the_log(tmp_path, monkeypatch):
    _use_steps(tmp_path, monkeypatch, [
        ("重抓", [sys.executable, "-c", "print('新增 3 份')"]),
        ("補卡片", [sys.executable, "-c", "print('目錄共 1800 份文件')"]),
    ])
    assert ud.main() == 0
    log = _log(tmp_path)
    assert log.index("新增 3 份") < log.index("目錄共 1800 份文件") < log.index("完成")


def test_a_failed_step_stops_the_rest(tmp_path, monkeypatch):
    _use_steps(tmp_path, monkeypatch, [
        ("重抓", [sys.executable, "-c", "import sys; sys.exit(3)"]),
        ("補卡片", [sys.executable, "-c", "print('不該執行')"]),
    ])
    assert ud.main() == 3
    log = _log(tmp_path)
    assert "重抓失敗（結束代碼 3）" in log
    assert "不該執行" not in log and "完成" not in log


def test_only_recent_logs_are_kept(tmp_path, monkeypatch):
    for i in range(ud.KEEP_LOGS + 3):
        (tmp_path / f"20260101_{i:06d}.log").write_text("舊紀錄", encoding="utf-8")
    _use_steps(tmp_path, monkeypatch, [("重抓", [sys.executable, "-c", "pass"])])
    ud.main()
    logs = sorted(tmp_path.glob("*.log"))
    assert len(logs) == ud.KEEP_LOGS
    assert not (tmp_path / "20260101_000000.log").exists()  # 最舊的先刪
