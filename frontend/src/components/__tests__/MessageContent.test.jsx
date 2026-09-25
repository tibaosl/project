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
});
