import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import GraduationCheck from "../GraduationCheck";
import MessageContent from "../MessageContent";

function item(overrides) {
  return { group: "", title: "", status: "todo", progress: "", ratio: null, lines: [], how: "", asks: [], note: "", ...overrides };
}

const DATA = {
  kind: "graduation_check",
  department: "資訊工程學系",
  cohort: "113",
  can_graduate: false,
  headline: "離畢業還有 3 項條件沒完成",
  summary: "2 項還要補、1 項照常在學就會完成。",
  hours_available: true,
  items: [
    item({
      group: "系訂必修學分",
      title: "一般系訂必修學分",
      progress: "45 / 58 學分・17 / 22 門",
      ratio: 0.77,
      lines: ["還差 13 學分、5 門", "要重修：演算法（114-2 停修）"],
      how: "審查表只列出修過的課，還沒修的必修要對照系上的必修科目表。",
      asks: [{ label: "問必修科目有哪些", question: "資訊工程學系 113 學年度入學，必修科目有哪些？" }],
    }),
    item({ group: "共同必修學分", title: "共同必修學分（整體）", lines: ["整體還差 5 學分、2 門"] }),
    item({ group: "其它學分", title: "操行", status: "auto", progress: "4 / 8 學期", lines: ["還差 4 學期"] }),
    item({ group: "特殊檢核", title: "通過英文能力鑑定考試", status: "done", note: "通過培力英檢考試" }),
  ],
};

describe("GraduationCheck", () => {
  it("顯示總結、還沒完成的條件（還差多少、怎麼補），完成的只列名稱", () => {
    render(<GraduationCheck data={DATA} />);

    expect(screen.getByText("離畢業還有 3 項條件沒完成")).toBeInTheDocument();
    expect(screen.getByText("2 項還要補、1 項照常在學就會完成。")).toBeInTheDocument();
    expect(screen.getByText("資訊工程學系・113 學年度入學")).toBeInTheDocument();

    const required = screen.getByText("一般系訂必修學分").closest(".ncux-card");
    expect(within(required).getByText("還要補")).toBeInTheDocument();
    expect(within(required).getByText(/系訂必修學分・45 \/ 58 學分/)).toBeInTheDocument();
    expect(within(required).getByText("・要重修：演算法（114-2 停修）")).toBeInTheDocument();
    expect(within(required).getByText(/還沒修的必修要對照系上的必修科目表/)).toBeInTheDocument();

    // 標題已經有類別名稱時不重複寫
    const whole = screen.getByText("共同必修學分（整體）").closest(".ncux-card");
    expect(within(whole).queryByText(/^ 共同必修學分/)).not.toBeInTheDocument();

    expect(screen.getByText("照常在學就好")).toBeInTheDocument();
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.getByText("（通過培力英檢考試）")).toBeInTheDocument();
    expect(screen.getByText("通過英文能力鑑定考試").closest(".ncux-card")).toBeNull();
  });

  it("點卡片上的問題會送出完整的問句，沒有 onAsk 時不顯示按鈕", async () => {
    const onAsk = vi.fn();
    const { rerender } = render(<GraduationCheck data={DATA} onAsk={onAsk} />);

    await userEvent.click(screen.getByRole("button", { name: "問必修科目有哪些" }));
    expect(onAsk).toHaveBeenCalledWith("資訊工程學系 113 學年度入學，必修科目有哪些？");

    rerender(<GraduationCheck data={DATA} />);
    expect(screen.queryByRole("button", { name: "問必修科目有哪些" })).not.toBeInTheDocument();
  });

  it("畢業條件都完成時顯示完成的橫幅", () => {
    const data = {
      ...DATA,
      can_graduate: true,
      headline: "畢業條件都完成了",
      summary: "實際能不能畢業以各學系跟教務處的審查為準。",
      items: [item({ title: "畢業總學分", status: "done", note: "130 / 128 學分" })],
    };
    const { container } = render(<GraduationCheck data={data} />);

    expect(container.querySelector(".ncux-banner-ok")).toHaveTextContent("畢業條件都完成了");
    expect(screen.queryByText("還沒完成的條件")).not.toBeInTheDocument();
  });

  it("MessageContent 依 kind 顯示這張卡片，並把 onAsk 傳下去", async () => {
    const onAsk = vi.fn();
    render(<MessageContent content={DATA} onAsk={onAsk} />);

    await userEvent.click(screen.getByRole("button", { name: "問必修科目有哪些" }));
    expect(onAsk).toHaveBeenCalledTimes(1);
  });

  it("畫面上的文字沒有分號", () => {
    const { container } = render(<GraduationCheck data={DATA} onAsk={() => {}} />);
    expect(container.textContent).not.toMatch(/[；;]/);
  });
});
