import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import MessageContent from "../MessageContent";

// 回歸測試：後端有些工具（例如活動報名紀錄）把多筆資料組成一長串純文字
// 用 "\n" 分行再丟給 <ReactMarkdown> 顯示。react-markdown 沒有加
// remark-breaks，純 CommonMark 規則下同一段落裡「單一個」換行符號會被當
// 成空白直接接在一起，導致排版擠成一整條——這是使用者實際回報過的真實
// bug（活動報名紀錄一開始就是這樣壞的）。修法是每個欄位都用 Markdown
// 項目符號（"- "）開頭，這裡驗證這種格式真的會被分別渲染成獨立的 <li>，
// 不會擠成一行。標題另外改用三級標題（"### "），驗證真的會渲染成 <h3>
// （不是清單項目），這樣不同筆資料之間才會有瀏覽器預設的標題留白當間隔，
// 字級也會跟內文有區別（使用者要求「標題大小不同」「活動之間要有間隔」）。
describe("MessageContent", () => {
  it("項目符號清單文字會渲染成多個獨立的 <li>，不會擠成一行", () => {
    const content = [
      "**你的活動報名紀錄（共 2 筆）：**",
      "",
      "### 【活動 A】（場次 A）",
      "- 報名狀態：正取",
      "- 地點：某教室",
      "",
      "### 【活動 B】（場次 B）",
      "- 報名狀態：正取",
    ].join("\n");

    render(<MessageContent content={content} />);

    const headings = screen.getAllByRole("heading", { level: 3 });
    expect(headings).toHaveLength(2);
    expect(headings[0]).toHaveTextContent("【活動 A】（場次 A）");
    expect(headings[1]).toHaveTextContent("【活動 B】（場次 B）");

    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("報名狀態：正取");
    expect(items[1]).toHaveTextContent("地點：某教室");
    // 不同欄位之間應該是各自獨立的文字節點，不會被黏在同一行裡。
    expect(items[0].textContent).not.toContain("地點");
  });

  it("純文字內容（不是清單）仍然正常顯示成 Markdown 段落", () => {
    render(<MessageContent content="一般法規回答文字" />);
    expect(screen.getByText("一般法規回答文字")).toBeInTheDocument();
  });
});
