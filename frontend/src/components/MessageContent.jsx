import ReactMarkdown from "react-markdown";
import HoursDashboard from "./HoursDashboard";
import ActivityList from "./ActivityList";
import ActivityDetail from "./ActivityDetail";
import ScheduleTable from "./ScheduleTable";
import AcademicAnalysis from "./AcademicAnalysis";

/**
 * 依內容形狀分派渲染方式，對應後端各個工具回傳的 content：
 * - 字串 → 當 Markdown 顯示（法規回答、選課結果、活動搜尋列表...）
 * - {kind: "hours_dashboard" | "activity_recommendations" | "activity_tag_search"
 *    | "activity_detail" | "activity_confirmation", ...} → 各自的卡片元件
 * - 課表的陣列（每筆有 day/period/time/details）→ 課表格線
 *
 * 跟舊版 ui.py 的 render_agent_reply() 是同一套邏輯，只是從 Streamlit
 * markdown 改寫成 React 元件。
 */
export default function MessageContent({ content }) {
  if (content == null) return null;

  if (typeof content === "string") {
    return (
      <div className="markdown-body">
        <ReactMarkdown>{content}</ReactMarkdown>
      </div>
    );
  }

  if (Array.isArray(content)) {
    if (content.length > 0 && content[0] && typeof content[0] === "object" && "day" in content[0]) {
      return <ScheduleTable courses={content} />;
    }
    // 不認得的陣列形狀，降級成 JSON 顯示方便除錯。
    return <pre style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>{JSON.stringify(content, null, 2)}</pre>;
  }

  if (typeof content === "object" && content.kind) {
    switch (content.kind) {
      case "hours_dashboard":
        return <HoursDashboard data={content} />;
      case "academic_analysis":
        return <AcademicAnalysis data={content} />;
      case "activity_recommendations":
      case "activity_tag_search":
        return <ActivityList data={content} />;
      case "activity_detail":
      case "activity_confirmation":
        return <ActivityDetail data={content} />;
      default:
        return <pre style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>{JSON.stringify(content, null, 2)}</pre>;
    }
  }

  return <pre style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>{JSON.stringify(content, null, 2)}</pre>;
}
