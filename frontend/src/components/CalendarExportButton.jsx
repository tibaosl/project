import { useState } from "react";
import { useOptionalSession } from "../context/SessionContext";
import { describeContents, downloadCalendar } from "../api/calendarExport";
import Icon from "./Icon";

/**
 * 下載行事曆檔（.ics）的按鈕，放在校曆、我的行程、匯出行事曆的卡片下面。
 * 有登入的話後端會加上這學期的課跟已報名的活動，所以按鈕文字跟著登入狀態變。
 */
export default function CalendarExportButton() {
  const session = useOptionalSession()?.session;
  const [state, setState] = useState({ status: "idle", message: "" });
  const loggedIn = !!session?.token;

  async function handleClick() {
    setState({ status: "busy", message: "" });
    try {
      const contents = await downloadCalendar(session?.token);
      const missing = loggedIn && !contents.includes("classes") ? "（這次沒有排進課表，可能是寒暑假還沒有下學期的校曆，或課務系統暫時連不上）" : "";
      setState({ status: "done", message: `已下載，內容有${describeContents(contents)}。${missing}` });
    } catch (error) {
      setState({ status: "error", message: error.message });
    }
  }

  const busy = state.status === "busy";
  return (
    <div className="calendar-export">
      <button type="button" className="calendar-export-button" onClick={handleClick} disabled={busy}>
        <Icon name="download" />
        <span>{busy ? "正在整理..." : loggedIn ? "下載課表跟校曆（.ics）" : "下載校曆（.ics）"}</span>
      </button>
      {state.message && (
        <p
          className={`ncux-card-meta calendar-export-message${state.status === "error" ? " is-error" : ""}`}
          role={state.status === "error" ? "alert" : "status"}
        >
          {state.message}
        </p>
      )}
    </div>
  );
}
