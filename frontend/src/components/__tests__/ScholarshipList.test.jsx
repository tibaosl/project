import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import MessageContent from "../MessageContent";

const profile = {
  department: "資訊工程學系",
  college: "資訊電機學院",
  degree: "學士班",
  grade: 3,
  latest_term: "114-2",
  latest_average: 86.5,
  latest_class_rank: "7/52",
  previous_year: 114,
  year_average: 85.31,
  cumulative_average: 83.2,
};

function scholarship(overrides) {
  return {
    name: "似鳥國際獎學金",
    kind: "獎學金",
    amount: "10 萬",
    quota: "7(大學部) 3(碩士班)",
    eligibility: "",
    period: "",
    deadline: "9/21",
    deadline_term: "115-1",
    deadline_passed: true,
    how_to_apply: "線上登錄申請，申請表件送生活輔導組。",
    notes: "",
    conduct_min: 85,
    status: "eligible",
    checks: [{ label: "限管理學院、資訊電機學院（你：資訊工程學系）", ok: true }],
    pending: [],
    sources: [{ title: "115學年度第1學期各項獎學金一覽表", file: "理學院/115-1獎學金一覽表.pdf", url: "https://www.ncu.edu.tw/" }],
    ...overrides,
  };
}

function envelope(overrides) {
  return {
    kind: "scholarship_recommendations",
    profile,
    statuses: [],
    eligible: [],
    maybe: [],
    excluded_count: 0,
    apply_url: "https://cis.ncu.edu.tw/Scholarship",
    ...overrides,
  };
}

describe("ScholarshipList", () => {
  it("符合資格的獎學金畫成卡片，列出每個條件怎麼判斷、截止日跟原始辦法", () => {
    render(<MessageContent content={envelope({ eligible: [scholarship({})], excluded_count: 30 })} />);

    expect(screen.getByText(/有 1 項獎學金你看起來符合資格/)).toBeInTheDocument();
    expect(screen.getByText(/資訊工程學系・學士班 3 年級・114-2 平均 86.50（班排名 7\/52）/)).toBeInTheDocument();
    expect(screen.getByText("似鳥國際獎學金")).toBeInTheDocument();
    expect(screen.getByText("限管理學院、資訊電機學院（你：資訊工程學系）")).toBeInTheDocument();
    expect(screen.getByText(/115-1 學期截止日 9\/21（已截止）/)).toBeInTheDocument();
    expect(screen.getByText(/另外要操行 85 分以上/)).toBeInTheDocument();
    expect(screen.getByText(/另外有 30 項/)).toBeInTheDocument();
    // data/ 底下的路徑每一段各自編碼，斜線要留著
    expect(screen.getByRole("link", { name: /115學年度第1學期各項獎學金一覽表/ })).toHaveAttribute(
      "href",
      `/files/${encodeURIComponent("理學院")}/${encodeURIComponent("115-1獎學金一覽表.pdf")}`,
    );
    expect(screen.getByRole("link", { name: "獎助學金暨工讀管理系統" })).toHaveAttribute(
      "href",
      "https://cis.ncu.edu.tw/Scholarship",
    );
  });

  it("還要確認條件的依條件分組，同時有好幾個條件時歸到身分條件那組", () => {
    const maybe = [
      scholarship({
        name: "外語檢定認證助學金",
        status: "maybe",
        deadline: "",
        pending: [
          { kind: "語言檢定", text: "通過外語檢定" },
          { kind: "經濟弱勢", text: "符合安心就學支持計畫身分" },
        ],
      }),
      scholarship({ name: "上詮光纖興學獎學金", status: "maybe", pending: [{ kind: "設籍地區", text: "設籍彰化縣" }] }),
    ];
    render(<MessageContent content={envelope({ maybe })} />);

    expect(screen.getByText(/目前沒有找到從成績單就能確定符合的獎學金，另外 2 項還要確認條件/)).toBeInTheDocument();
    const summaries = screen.getAllByText(/（1 項）$/);
    expect(summaries.map((s) => s.textContent)).toEqual([
      "要有經濟弱勢身分（清寒、低收入戶等）（1 項）",
      "要設籍在特定地區（1 項）",
    ]);
    expect(screen.getByText("要確認：設籍彰化縣")).toBeInTheDocument();
  });

  it("同一個獎學金的好幾個獎項併成一張卡片，列出各獎項的金額", () => {
    const card = scholarship({
      name: "工學院提升學生英語能力獎勵",
      amount: "",
      quota: "",
      status: "maybe",
      tiers: [
        { name: "多益TOEIC 800分以上", amount: "NT$ 1500 元", conditions: ["多益 800 分"] },
        { name: "托福iBT 90分以上", amount: "NT$ 1500 元", conditions: [] },
      ],
      pending: [
        { kind: "語言檢定", text: "在學期間通過英檢" },
        { kind: "語言檢定", text: "多益 800 分" },
      ],
    });
    render(<MessageContent content={envelope({ maybe: [card] })} />);
    expect(screen.getByText("多益TOEIC 800分以上：NT$ 1500 元（條件：多益 800 分）")).toBeInTheDocument();
    expect(screen.getByText("托福iBT 90分以上：NT$ 1500 元")).toBeInTheDocument();
    expect(screen.queryByText(/💰/)).not.toBeInTheDocument();
    // 獎項自己的條件不在下面重複列，改成提醒看上面
    expect(screen.getByText("要確認：在學期間通過英檢")).toBeInTheDocument();
    expect(screen.queryByText("要確認：多益 800 分")).not.toBeInTheDocument();
    expect(screen.getByText("要確認：上面各獎項的條件")).toBeInTheDocument();
  });

  it("說明過的身分會顯示在上面", () => {
    render(<MessageContent content={envelope({ statuses: ["經濟弱勢"], eligible: [scholarship({})] })} />);
    expect(screen.getByText("已經算進你說明的身分：經濟弱勢")).toBeInTheDocument();
  });
});
