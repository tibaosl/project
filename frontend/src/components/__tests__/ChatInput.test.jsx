import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import ChatInput from "../ChatInput";

describe("ChatInput", () => {
  it("送出並清空輸入框：點擊送出按鈕", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} disabled={false} />);

    const input = screen.getByPlaceholderText(/請輸入你的問題/);
    await user.type(input, "資工系英文畢業門檻");
    await user.click(screen.getByRole("button", { name: "送出" }));

    expect(onSend).toHaveBeenCalledWith("資工系英文畢業門檻");
    expect(input).toHaveValue("");
  });

  it("送出並清空輸入框：按 Enter 鍵", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} disabled={false} />);

    const input = screen.getByPlaceholderText(/請輸入你的問題/);
    await user.type(input, "客家系呢{Enter}");

    expect(onSend).toHaveBeenCalledWith("客家系呢");
    expect(input).toHaveValue("");
  });

  it("空白或全部是空白字元時不送出", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} disabled={false} />);

    const input = screen.getByPlaceholderText(/請輸入你的問題/);
    await user.type(input, "   {Enter}");

    expect(onSend).not.toHaveBeenCalled();
  });

  it("disabled 時 Enter 鍵不會送出", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    const { rerender } = render(<ChatInput onSend={onSend} disabled={false} />);

    const input = screen.getByPlaceholderText(/請輸入你的問題/);
    await user.type(input, "測試");
    rerender(<ChatInput onSend={onSend} disabled={true} />);
    await user.type(input, "{Enter}");

    expect(onSend).not.toHaveBeenCalled();
  });
});
