import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import CachedAnswerNote, { cachedDayLabel } from "../CachedAnswerNote";

describe("CachedAnswerNote", () => {
  it("今天整理的寫「今天」，其他寫月/日，看不懂的時間不寫日期", () => {
    const now = new Date(2026, 9, 19, 15, 0);
    expect(cachedDayLabel("2026-10-19T08:30:00", now)).toBe("今天");
    expect(cachedDayLabel("2026-10-18T21:03:11", now)).toBe("10/18");
    expect(cachedDayLabel("不是時間", now)).toBe("");
  });

  it("顯示整理的日期，按「重新查詢」會呼叫 onRefresh", async () => {
    const onRefresh = vi.fn();
    render(<CachedAnswerNote created="2026-10-18T21:03:11" onRefresh={onRefresh} />);

    expect(screen.getByText("10/18 整理好的回答")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "重新查詢這則回答" }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("還在處理別的問題時不能按，沒給 onRefresh 就只顯示說明", () => {
    const { rerender } = render(<CachedAnswerNote created="2026-10-18T21:03:11" onRefresh={vi.fn()} disabled />);
    expect(screen.getByRole("button", { name: "重新查詢這則回答" })).toBeDisabled();

    rerender(<CachedAnswerNote created="壞掉的時間" />);
    expect(screen.getByText("之前整理好的回答")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("CachedAnswerNote 標準答案", () => {
  it("標準答案標出是標準答案跟整理的日期", () => {
    render(<CachedAnswerNote created="2026-10-18" standard onRefresh={vi.fn()} />);
    expect(screen.getByText("標準答案（10/18 整理）")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新查詢這則回答" })).toBeInTheDocument();
  });
});
