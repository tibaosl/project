"""agent_tools.py 的工具 schema 基本檢查。

不需要真的登入 NCU 帳號、也不會打任何網路——只檢查 build_tools() 組出來的
工具清單本身有沒有基本問題（重複名稱、缺 docstring、報名/取消報名工具
不小心多開放了一個 confirm 參數給模型...）。目的是之後新增/修改工具時，
能及早攔住手滑，不用每次都要真的跑一次完整對話才發現。

用 pytest 執行：pytest test_agent_tools_schema.py
"""

from agent_tools import build_tools


def _tools():
    # 沒有帳密也要能正常組出完整工具清單——缺帳密的檢查是留到工具真的
    # 被呼叫的當下才做（見各工具內部的 NO_CREDENTIALS_MSG 分支），
    # 不應該影響工具清單本身的建置，所以這裡刻意用空字串測。
    return build_tools(username="", password="", history_str="無")


def test_tool_names_are_unique():
    names = [t.name for t in _tools()]
    assert len(names) == len(set(names)), f"工具名稱重複：{names}"


def test_every_tool_has_a_description():
    for t in _tools():
        assert t.description and t.description.strip(), f"工具 {t.name} 沒有 docstring"


def test_registration_and_cancellation_tools_never_expose_a_confirm_argument():
    # 安全機制的核心：報名/取消報名工具開放給模型的參數 schema 裡，絕對不能
    # 出現任何可以讓模型自己決定「要不要真的送出」的欄位（例如 confirm）。
    # 真正送出永遠只能透過 supervisor_agent.py 裡的關鍵字攔截觸發，見
    # agent_node 開頭對 CONFIRM_KEYWORDS 的說明。
    preview_tool_names = {"preview_activity_registration", "preview_activity_cancellation"}
    for t in _tools():
        if t.name not in preview_tool_names:
            continue
        assert "confirm" not in t.args, (
            f"{t.name} 的參數裡出現了 confirm，這會讓模型可以自己決定要不要真的送出！"
        )


def test_no_tool_takes_username_or_password_as_a_model_supplied_argument():
    # 帳密要用 closure 包進工具裡（見 build_tools 內部），不能是模型自己
    # 填的參數——不然帳密就會出現在模型看得到、也可能被模型填錯/外洩的
    # 參數 schema 裡。
    for t in _tools():
        assert "username" not in t.args and "password" not in t.args, (
            f"{t.name} 的參數 schema 裡出現了 username/password：{t.args}"
        )


def test_expected_tools_are_all_registered():
    expected = {
        "get_my_schedule",
        "search_course_catalog",
        "get_my_hours_dashboard",
        "get_my_academic_analysis",
        "search_campus_activities",
        "get_activity_details",
        "recommend_activities_for_my_deficiencies",
        "find_activities_by_hour_category",
        "preview_activity_registration",
        "preview_activity_cancellation",
        "search_campus_regulations",
    }
    names = {t.name for t in _tools()}
    assert expected <= names, f"缺少預期的工具：{expected - names}"
