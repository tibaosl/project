import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import MessageList from "../MessageList";

describe("MessageList", () => {
  it("使用者訊息顯示使用者頭像、助手訊息顯示助手頭像", () => {
    const messages = [
      { id: "1", role: "assistant", content: "你好！" },
      { id: "2", role: "user", content: "資工系英文畢業門檻" },
    ];
    const { container } = render(<MessageList messages={messages} />);

    expect(container.querySelector(".chat-avatar-assistant")).toBeInTheDocument();
    expect(container.querySelector(".chat-avatar-user")).toBeInTheDocument();
    expect(screen.getByText("你好！")).toBeInTheDocument();
    expect(screen.getByText("資工系英文畢業門檻")).toBeInTheDocument();
  });

  it("助手訊息還在跑 status 時顯示狀態文字，不顯示「沒有取得回覆」", () => {
    const messages = [
      { id: "1", role: "assistant", content: null, status: "正在查詢校園法規...", isStreaming: true },
    ];
    render(<MessageList messages={messages} />);

    expect(screen.getByText("正在查詢校園法規...")).toBeInTheDocument();
    expect(screen.queryByText("（沒有取得回覆）")).not.toBeInTheDocument();
  });

  it("助手訊息完全沒有內容、也沒在串流時顯示「沒有取得回覆」", () => {
    const messages = [
      { id: "1", role: "assistant", content: null, status: null, isStreaming: false },
    ];
    render(<MessageList messages={messages} />);

    expect(screen.getByText("（沒有取得回覆）")).toBeInTheDocument();
  });

  it("只在最新一則回覆下面顯示「你可能還想問」，點了會送出該問題", async () => {
    const onAsk = vi.fn();
    const messages = [
      { id: "1", role: "user", content: "第一題" },
      { id: "2", role: "assistant", content: "舊回答", suggestions: ["舊的追問"] },
      { id: "3", role: "user", content: "第二題" },
      { id: "4", role: "assistant", content: "新回答", suggestions: ["我的累計排名是多少？", "我有沒有需要重修的課？"] },
    ];
    render(<MessageList messages={messages} onAsk={onAsk} />);

    expect(screen.getByText("你可能還想問")).toBeInTheDocument();
    expect(screen.queryByText("舊的追問")).not.toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("button", { name: "我的累計排名是多少？" }));
    expect(onAsk).toHaveBeenCalledWith("我的累計排名是多少？");
  });

  it("還在處理新問題時不顯示追問", () => {
    const messages = [{ id: "1", role: "assistant", content: "回答", suggestions: ["追問"] }];
    render(<MessageList messages={messages} onAsk={vi.fn()} busy />);

    expect(screen.queryByText("你可能還想問")).not.toBeInTheDocument();
  });
});
