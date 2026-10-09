import { render, screen, within } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import MessageContent, { ensureBlankLineBeforeTables } from "../MessageContent";

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

  // 回歸測試：法規回答的門檻/費用對照是 Markdown 表格，沒加 remark-gfm 時整張表
  // 會變成一段夾著 | 的文字（使用者實際回報過）。
  it("Markdown 表格會渲染成真正的表格", () => {
    const content = [
      "資電學院英文門檻如下：",
      "",
      "| 英檢考試 | 資電學院門檻 |",
      "|---|---|",
      "| 多益（聽讀測驗） | 600（114年前適用） |",
      "| 雅思 | 5.5 |",
    ].join("\n");

    render(<MessageContent content={content} />);

    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("columnheader").map((th) => th.textContent)).toEqual(["英檢考試", "資電學院門檻"]);
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(within(table).getByRole("cell", { name: "5.5" })).toBeInTheDocument();
  });

  it("表格緊接在清單項目後面、沒有空行時也會渲染成表格", () => {
    const content = ["- 資電學院英文門檻如下：[1]", "| 英檢考試 | 門檻 |", "|---|---|", "| 雅思 | 5.5 |"].join("\n");

    render(<MessageContent content={content} />);

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("listitem")).toHaveTextContent("資電學院英文門檻如下：[1]");
    expect(screen.getByRole("listitem")).not.toHaveTextContent("雅思");
  });

  // 回歸測試：CommonMark 判斷 ** 能不能開始、結束粗體時把全形標點（）「」：等）當成標點，
  // 結尾的 ** 前面是全形標點、後面緊接中文字就關不起來（開頭的 ** 反過來也一樣），
  // 網頁上會出現字面上的 **。2026-10-06 實測問「導師密碼是什麼？」，回答最後一句就是這樣。
  it.each([
    [
      "結尾的 ** 前面是全形括號",
      "以上是全校適用，以**115-1學期選課相關資訊（2026/05/15更新）**及選課問答集的說明為準。",
      "115-1學期選課相關資訊（2026/05/15更新）",
    ],
    ["整段粗體用全形括號包起來", "**（注意）**請先完成導師確認", "（注意）"],
    ["開頭的 ** 後面是全形引號", "請向導師索取**「導師密碼」**。", "「導師密碼」"],
    ["粗體以全形冒號結尾", "- **申請資格：**大學部學生", "申請資格："],
  ])("粗體旁邊是全形標點時也會顯示成粗體（%s）", (_, content, bold) => {
    const { container } = render(<MessageContent content={content} />);
    expect(container.querySelector("strong")?.textContent).toBe(bold);
    expect(container.textContent).not.toContain("**");
  });

  it("行內 code 裡的 ** 照原樣顯示，不會變成粗體", () => {
    const { container } = render(<MessageContent content="格式寫成 `**（注意）**請先` 就好" />);
    expect(container.querySelector("code")).toHaveTextContent("**（注意）**請先");
    expect(container.querySelector("strong")).toBeNull();
  });
});

describe("ensureBlankLineBeforeTables", () => {
  it("只在表格前面缺空行時補一行，不動表格本身跟其他內容", () => {
    const table = ["| a | b |", "|---|---|", "| 1 | 2 |"];
    expect(ensureBlankLineBeforeTables(["如下：", ...table].join("\n"))).toBe(["如下：", "", ...table].join("\n"));
    expect(ensureBlankLineBeforeTables(["如下：", "", ...table].join("\n"))).toBe(["如下：", "", ...table].join("\n"));
    expect(ensureBlankLineBeforeTables(table.join("\n"))).toBe(table.join("\n"));
    // 開頭是 | 但下一行不是分隔列，就不是表格
    expect(ensureBlankLineBeforeTables("說明\n| 只是一行字")).toBe("說明\n| 只是一行字");
  });
});

const academicAnalysis = {
  kind: "academic_analysis",
  department: "測試學系",
  grade: "三年A班",
  credits: {
    earned: 74,
    required: 128,
    remaining: 54,
    passed_threshold: false,
    by_course_type: { 必修: 56, 通識: 9 },
  },
  gpa: {
    cumulative_average: 85.55,
    latest_change: -1.15,
    semesters: [
      { term: "1141", label: "114-1", average: 82.84, earned_credits: 19, class_rank: "37/52", dept_rank: "79/106" },
      { term: "1142", label: "114-2", average: 81.69, earned_credits: 16, class_rank: "46/52", dept_rank: "92/105" },
    ],
    cumulative_ranks: [
      { term: "1142", label: "114-2", average: 85.55, class_rank: "28/52", dept_rank: "59/105" },
      { term: "1151", label: "115-1", average: 85.55, class_rank: "27/52", dept_rank: "58/105" },
    ],
  },
  graduation_available: true,
  graduation_categories: [
    {
      name: "系訂必修學分",
      passed: false,
      required_credits: 58,
      earned_credits: 45,
      required_courses: 22,
      earned_courses: 17,
      percentage: 77.59,
      unmet_rules: [{ name: "一般系訂必修學分-A類", remaining_credits: 13, remaining_courses: 5, memo: "" }],
    },
    {
      name: "其它學分",
      passed: false,
      required_credits: 0,
      earned_credits: 1,
      required_courses: 15,
      earned_courses: 11,
      percentage: 73.33,
      unmet_rules: [{ name: "學生學習護照", remaining_credits: 0, remaining_courses: 0, memo: "總時數:91.0(尚未達標)" }],
    },
  ],
  alerts: [
    {
      term: "1142", label: "114-2", course_no: "CE3005", name: "演算法", credits: 3,
      course_type: "必修", score_text: "停修", reason: "停修", required: true,
    },
    {
      term: "1141", label: "114-1", course_no: "LN9001", name: "被當的選修", credits: 3,
      course_type: "選修", score_text: "50.00", reason: "不及格", required: false,
    },
  ],
};

describe("MessageContent 學業分析卡片", () => {
  it("overview：學分缺口、重修提醒、畢業類別、成績變化、累計排名都顯示", () => {
    render(<MessageContent content={academicAnalysis} />);

    expect(screen.getByText(/已修 74 \/ 128 學分，還差 54 學分/)).toBeInTheDocument();
    expect(screen.getByText("演算法")).toBeInTheDocument();
    expect(screen.getByText("被當的選修")).toBeInTheDocument();
    expect(screen.getByText(/必修課需要重新修習/)).toBeInTheDocument();
    expect(screen.getByText(/一般系訂必修學分-A類：還差 13 學分、5 門/)).toBeInTheDocument();
    // 學分、門數都不缺但沒通過（例如學習護照時數不夠）要顯示原因，不能寫「還差 0」
    expect(screen.getByText(/學生學習護照：條件尚未達成（總時數:91.0\(尚未達標\)）/)).toBeInTheDocument();
    expect(screen.getByText("▼ 1.15")).toBeInTheDocument();
    expect(screen.getByText("累計排名")).toBeInTheDocument();
    expect(screen.getByText("58/105")).toBeInTheDocument();
  });

  it("credits：只回答學分，不顯示成績趨勢跟排名，只提會卡畢業的必修", () => {
    render(<MessageContent content={{ ...academicAnalysis, focus: "credits" }} />);

    expect(screen.getByText(/還差 54 學分/)).toBeInTheDocument();
    expect(screen.getByText("畢業類別進度")).toBeInTheDocument();
    expect(screen.getByText("尚未通過的必修")).toBeInTheDocument();
    expect(screen.getByText("演算法")).toBeInTheDocument();
    expect(screen.queryByText("被當的選修")).not.toBeInTheDocument();
    expect(screen.queryByText("成績趨勢")).not.toBeInTheDocument();
    expect(screen.queryByText("累計排名")).not.toBeInTheDocument();
  });

  it("credits：沒有未通過的必修時，整個提醒區塊都不顯示", () => {
    render(
      <MessageContent
        content={{ ...academicAnalysis, focus: "credits", alerts: academicAnalysis.alerts.filter((a) => !a.required) }}
      />,
    );
    expect(screen.queryByText("尚未通過的必修")).not.toBeInTheDocument();
    expect(screen.queryByText(/沒有不及格或停修/)).not.toBeInTheDocument();
  });

  it("grades：只回答成績，顯示最新累計排名跟成績趨勢，不顯示學分缺口", () => {
    render(<MessageContent content={{ ...academicAnalysis, focus: "grades" }} />);

    expect(screen.getByText(/累計排名 班 27\/52、系 58\/105/)).toBeInTheDocument();
    expect(screen.getByText(/排名截至 115-1/)).toBeInTheDocument();
    expect(screen.getByText("成績趨勢")).toBeInTheDocument();
    expect(screen.getByText("被當的選修")).toBeInTheDocument();
    expect(screen.queryByText(/還差 54 學分/)).not.toBeInTheDocument();
    expect(screen.queryByText("畢業類別進度")).not.toBeInTheDocument();
  });

  it("取不到畢業審查表時只顯示已修學分，不顯示畢業類別", () => {
    render(
      <MessageContent
        content={{
          ...academicAnalysis,
          graduation_available: false,
          graduation_categories: [],
          credits: { ...academicAnalysis.credits, required: null, remaining: null, passed_threshold: null },
        }}
      />,
    );

    expect(screen.getByText(/已修 74 學分（畢業資格審查表暫時取不到/)).toBeInTheDocument();
    expect(screen.queryByText("畢業類別進度")).not.toBeInTheDocument();
  });

  it("沒有需要重修的課時顯示通過提示", () => {
    render(<MessageContent content={{ ...academicAnalysis, alerts: [] }} />);
    expect(screen.getByText(/沒有不及格或停修/)).toBeInTheDocument();
  });

  it("沒有學分、門數要求也還沒修到的類別不顯示「學分 0・門數 0」", () => {
    const special = {
      name: "特殊檢核", passed: false, required_credits: 0, earned_credits: 0, required_courses: 0, earned_courses: 0,
      percentage: 66.67, unmet_rules: [{ name: "資電學院選修總學分", remaining_credits: 21, remaining_courses: 0, memo: "" }],
    };
    render(
      <MessageContent
        content={{ ...academicAnalysis, graduation_categories: [...academicAnalysis.graduation_categories, special] }}
      />,
    );
    expect(screen.getByText("特殊檢核")).toBeInTheDocument();
    expect(screen.queryByText(/學分 0・門數 0/)).not.toBeInTheDocument();
    expect(screen.getByText(/資電學院選修總學分：還差 21 學分/)).toBeInTheDocument();
    // 只有其中一項是 0 的照常顯示
    expect(screen.getByText(/學分 1・門數 11 \/ 15/)).toBeInTheDocument();
  });
});

describe("MessageContent 課表", () => {
  const course = (day, period, time, details) => ({ day, period, time, raw_content: details.join("\n"), details });

  it("清單以外的節次跟星期日有課也要顯示，照上課時間排在對的位置", () => {
    render(
      <MessageContent
        content={[
          course("星期一", "第二節", "09:00 09:50", ["CE1001-*", "程式設計", "E6-A101"]),
          course("星期三", "第Z節", "12:00 12:50", ["PE1001-*", "午間桌球", "體育館"]),
          course("星期日", "第五節", "13:00 13:50", ["GS1001-*", "週日通識", "E1-101"]),
        ]}
      />,
    );
    expect(screen.getByText("午間桌球")).toBeInTheDocument();
    expect(screen.getByText("週日通識")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "星期日" })).toBeInTheDocument();
    const rows = screen.getAllByRole("row").map((row) => row.textContent);
    const lunch = rows.findIndex((text) => text.startsWith("第Ｚ節"));
    expect(lunch).toBeGreaterThan(rows.findIndex((text) => text.startsWith("第四節")));
    expect(lunch).toBeLessThan(rows.findIndex((text) => text.startsWith("第五節")));
  });
});

describe("MessageContent 報名確認", () => {
  it("確認卡片寫出要報名的是哪一個場次", () => {
    const session = {
      session_id: "event2", session_name: "AI時代的關鍵能力與藍海策略", registration_mode: "online",
      event_period: "2026-11-17 15:00 ~ 2026-11-17 17:00",
    };
    render(
      <MessageContent
        content={{
          kind: "activity_confirmation", action_label: "報名", title: "115-1人本AI論壇",
          sessions: [session], session_name: session.session_name,
        }}
      />,
    );
    expect(screen.getByText(/確定要報名「AI時代的關鍵能力與藍海策略」這個場次嗎？/)).toBeInTheDocument();
    expect(screen.getByText(/請回覆「確定報名」來送出/)).toBeInTheDocument();
  });
});
