import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import MessageList from "../MessageList";

describe("MessageList", () => {
  it("使用者訊息顯示 🧑 頭像、助手訊息顯示 🤖 頭像", () => {
    const messages = [
      { id: "1", role: "assistant", content: "你好！" },
      { id: "2", role: "user", content: "資工系英文畢業門檻" },
    ];
    render(<MessageList messages={messages} />);

    expect(screen.getByText("🤖")).toBeInTheDocument();
    expect(screen.getByText("🧑")).toBeInTheDocument();
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
});
