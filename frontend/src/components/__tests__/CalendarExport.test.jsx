import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import MessageContent from "../MessageContent";

const card = {
  kind: "calendar_export",
  title: "匯出到行事曆",
  logged_in: false,
  calendar_events: 42,
  term: null,
  source: "教務處/115 學年度校曆.pdf",
};

function mockDownload(response) {
  vi.spyOn(global, "fetch").mockResolvedValue(response);
  URL.createObjectURL = vi.fn(() => "blob:calendar");
  URL.revokeObjectURL = vi.fn();
  return vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
}

describe("匯出行事曆卡片", () => {
  afterEach(() => vi.restoreAllMocks());

  it("沒登入只說明校曆，並提示登入後可以加上課表", () => {
    render(<MessageContent content={card} />);
    expect(screen.getByText(/整年的校曆（42 件事/)).toBeInTheDocument();
    expect(screen.getByText(/登入之後還可以加上這學期的課/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /下載校曆/ })).toBeInTheDocument();
    expect(screen.getByText(/匯入與匯出/)).toBeInTheDocument();
  });

  it("有登入時列出這學期的上課期間", () => {
    render(<MessageContent content={{ ...card, logged_in: true, term: ["9/7（一）", "1/15（五）"] }} />);
    expect(screen.getByText(/這學期每週的課（9\/7（一）到1\/15（五）/)).toBeInTheDocument();
    expect(screen.getByText("已報名的活動場次")).toBeInTheDocument();
  });

  it("按下按鈕會下載檔案並說明內容", async () => {
    const click = mockDownload({
      ok: true,
      status: 200,
      headers: new Headers({ "X-Calendar-Contents": "calendar" }),
      blob: async () => new Blob(["BEGIN:VCALENDAR"]),
    });
    render(<MessageContent content={card} />);
    fireEvent.click(screen.getByRole("button", { name: /下載校曆/ }));

    expect(await screen.findByText("已下載，內容有校曆。")).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/calendar/export",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ token: "" }) }),
    );
    expect(click).toHaveBeenCalledTimes(1);
    expect(URL.createObjectURL).toHaveBeenCalled();
  });

  it("後端回錯誤時顯示原因", async () => {
    mockDownload({ ok: false, status: 401, json: async () => ({ detail: "登入狀態已經失效，請重新登入後再匯出。" }) });
    render(<MessageContent content={card} />);
    fireEvent.click(screen.getByRole("button", { name: /下載校曆/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("登入狀態已經失效");
  });

  it("校曆跟我的行程卡片下面也有下載按鈕", () => {
    render(<MessageContent content={{ kind: "campus_calendar", title: "校曆：期中", events: [] }} />);
    expect(screen.getByRole("button", { name: /下載校曆/ })).toBeInTheDocument();
  });
});
