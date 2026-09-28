import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
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

// 表格分隔列，例如 "|---|---|"、"| :--- | ---: |"
const TABLE_DELIMITER_ROW = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

/**
 * 表格前面沒有空行時（例如緊接在「門檻如下：」或清單項目的下一行），
 * Markdown 會把整張表當成上一段文字的延續，擠成一整行字。渲染前補一個空行。
 */
export function ensureBlankLineBeforeTables(text) {
  const lines = text.split("\n");
  const output = [];
  lines.forEach((line, i) => {
    const startsTable = line.trim().startsWith("|") && TABLE_DELIMITER_ROW.test(lines[i + 1] ?? "");
    const previous = output[output.length - 1];
    if (startsTable && previous !== undefined && previous.trim() !== "" && !previous.trim().startsWith("|")) {
      output.push("");
    }
    output.push(line);
  });
  return output.join("\n");
}

// 表格包一層可以左右捲動的容器，欄位多的表格在窄螢幕上不會把對話泡泡撐破。
function ScrollableTable({ node, ...props }) {
  return (
    <div className="markdown-table-wrap">
      <table {...props} />
    </div>
  );
}

export default function MessageContent({ content }) {
  if (content == null) return null;

  if (typeof content === "string") {
    // remark-gfm：react-markdown 預設只支援 CommonMark，表格要靠它才會畫成 <table>，
    // 不然整張表會變成一段夾著 | 的文字。
    return (
      <div className="markdown-body">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ table: ScrollableTable }}>
          {ensureBlankLineBeforeTables(content)}
        </ReactMarkdown>
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
