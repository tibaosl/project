"""後端啟動時在背景預先載入法規文件目錄跟校曆（main.warm_up），不會連到 OpenAI：
    python -m pytest tests/test_main_startup.py
"""

import os
import threading

os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")  # 匯入 main 時建立模型物件要有值

from fastapi.testclient import TestClient  # noqa: E402

from backend import main  # noqa: E402


def test_startup_runs_the_warm_up_in_the_background(monkeypatch):
    started = threading.Event()
    monkeypatch.setattr(main, "warm_up", started.set)
    with TestClient(main.app, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert started.wait(5)


def test_warm_up_failures_do_not_stop_the_server(monkeypatch):
    def broken():
        raise RuntimeError("data/ 壞掉了")

    monkeypatch.setattr(main.academic_agent, "get_catalog", broken)
    main.warm_up()  # 不會丟出例外，第一次查詢時會再載入


def test_warm_up_builds_the_search_index_and_calendar(monkeypatch):
    calls = []
    monkeypatch.setattr(main.academic_agent, "get_catalog", lambda: ["doc"])
    monkeypatch.setattr(main.academic_agent, "search_index", lambda catalog: calls.append(("index", catalog)))
    monkeypatch.setattr(main, "load_calendar", lambda: calls.append(("calendar",)))
    main.warm_up()
    assert calls == [("index", ["doc"]), ("calendar",)]
