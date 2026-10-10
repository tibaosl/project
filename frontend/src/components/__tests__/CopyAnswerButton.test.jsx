import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, afterEach } from "vitest";
import MessageList from "../MessageList";
import { answerWithSources } from "../CopyAnswerButton";

describe("複製回答", () => {
  afterEach(() => vi.restoreAllMocks());

  it("文字回答講完才有複製按鈕，卡片、串流中、使用者訊息都沒有", () => {
    const messages = [
      { id: "1", role: "user", content: "學生證不見了怎麼辦？" },
      { id: "2", role: "assistant", content: "到註冊組補辦。", isStreaming: false },
      { id: "3", role: "assistant", content: { kind: "campus_calendar", title: "校曆", events: [] } },
      { id: "4", role: "assistant", content: "正在打字", isStreaming: true },
    ];
    render(<MessageList messages={messages} />);
    expect(screen.getAllByRole("button", { name: "複製這則回答" })).toHaveLength(1);
  });

  it("複製畫面上的文字（不含 Markdown 符號），並附上參考資料的文件名稱", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    const messages = [{
      id: "1",
      role: "assistant",
      content: "**要到註冊組**補辦，工本費 200 元。",
      sources: ["教務處註冊組/學生證遺失補發申請作業.pdf (p.1)", "教務處註冊組/學生證遺失補發申請作業.pdf (p.2)"],
    }];
    render(<MessageList messages={messages} />);

    await user.click(screen.getByRole("button", { name: "複製這則回答" }));
    expect(writeText).toHaveBeenCalledWith("要到註冊組補辦，工本費 200 元。\n\n參考資料：學生證遺失補發申請作業");
    expect(await screen.findByText("已複製")).toBeInTheDocument();
  });

  it("沒有參考資料時只複製回答", () => {
    expect(answerWithSources("回答", [])).toBe("回答");
    expect(answerWithSources("回答", undefined)).toBe("回答");
  });
});
