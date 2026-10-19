import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, it, expect, vi } from "vitest";
import RemindersCard from "../RemindersCard";

const CALENDAR = {
  kind: "calendar", when: "明天", title: "受理課程停修申請", badge: "選課", tone: "info",
  detail: "到 11/27（五）", hint: "", ask: "",
};
const ACTIVITY = {
  kind: "activity", when: "明天 12:00", title: "職涯講座", badge: "活動", tone: "info",
  detail: "國鼎圖書資料館 3F", hint: "記得簽到、簽退，不然可能拿不到時數", ask: "我接下來 7 天有什麼行程？",
};

function reply(overrides = {}) {
  return {
    ok: true,
    json: async () => ({
      title: "最近要注意", range: "10/18（日）～10/24（六）", logged_in: true, status: "ready",
      loading: [], items: [CALENDAR], notes: [], ...overrides,
    }),
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("RemindersCard", () => {
  it("沒登入只有校曆，並提醒登入後還有什麼", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(reply({ logged_in: false }));
    render(<RemindersCard token="" isGuest onAsk={() => {}} />);

    expect(await screen.findByText("受理課程停修申請")).toBeInTheDocument();
    expect(screen.getByText("10/18（日）～10/24（六）")).toBeInTheDocument();
    expect(screen.getByText(/登入後還會提醒你報名的活動/)).toBeInTheDocument();
    // 校曆沒有後續問題，不是按鈕
    expect(screen.queryByRole("button", { name: /受理課程停修申請/ })).not.toBeInTheDocument();
    expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({ token: "" });
  });

  it("背景還在準備時顯示進度，過幾秒再問一次", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(reply({ status: "loading", loading: ["活動報名紀錄", "時數紀錄"] }))
      .mockResolvedValueOnce(reply({ items: [CALENDAR, ACTIVITY] }));
    render(<RemindersCard token="tok" onAsk={() => {}} />);

    expect(await screen.findByText("正在整理你的活動報名紀錄、時數紀錄…")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(2500));
    expect(await screen.findByText("職涯講座")).toBeInTheDocument();
    expect(screen.queryByText(/正在整理/)).not.toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledTimes(2);
  });

  it("點有後續問題的提醒就直接問", async () => {
    const user = userEvent.setup();
    const onAsk = vi.fn();
    vi.spyOn(global, "fetch").mockResolvedValue(reply({ items: [CALENDAR, ACTIVITY] }));
    render(<RemindersCard token="tok" onAsk={onAsk} />);

    await user.click(await screen.findByRole("button", { name: /職涯講座/ }));
    expect(onAsk).toHaveBeenCalledWith("我接下來 7 天有什麼行程？");
    expect(screen.getByText("記得簽到、簽退，不然可能拿不到時數")).toBeInTheDocument();
  });

  it("提醒很多時全部放在卡片裡的清單，標題列寫出幾項", async () => {
    const items = Array.from({ length: 7 }, (_, i) => ({ ...CALENDAR, title: `事件${i}` }));
    vi.spyOn(global, "fetch").mockResolvedValue(reply({ items }));
    render(<RemindersCard token="tok" onAsk={() => {}} />);

    expect(await screen.findByText("事件6")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(7);
    expect(screen.getByRole("button", { name: "最近要注意（7）" })).toHaveAttribute("aria-expanded", "true");
  });

  it("點標題列收起來只剩標題，重新整理後還記得", async () => {
    const user = userEvent.setup();
    vi.spyOn(global, "fetch").mockResolvedValue(reply({ items: [CALENDAR, ACTIVITY] }));
    const { unmount } = render(<RemindersCard token="tok" onAsk={() => {}} />);

    const toggle = await screen.findByRole("button", { name: "最近要注意（2）" });
    expect(await screen.findByText("職涯講座")).toBeVisible();
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("職涯講座")).not.toBeVisible();
    expect(screen.getByText("10/18（日）～10/24（六）")).not.toBeVisible();
    expect(localStorage.getItem("ncuxplore_reminders_collapsed")).toBe("1");

    unmount();
    render(<RemindersCard token="tok" onAsk={() => {}} />);
    const again = await screen.findByRole("button", { name: "最近要注意（2）" });
    expect(again).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("職涯講座")).not.toBeVisible();

    await user.click(again);
    expect(again).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("職涯講座")).toBeVisible();
    expect(localStorage.getItem("ncuxplore_reminders_collapsed")).toBeNull();
  });

  it("瀏覽器不讓存 localStorage 時一樣可以收起展開", async () => {
    const user = userEvent.setup();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    vi.spyOn(global, "fetch").mockResolvedValue(reply());
    render(<RemindersCard token="tok" onAsk={() => {}} />);

    const toggle = await screen.findByRole("button", { name: "最近要注意（1）" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("受理課程停修申請")).not.toBeVisible();
  });

  it("沒有要注意的事跟沒抓到的部分都說清楚", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      reply({ items: [], notes: ["這次沒有抓到你的時數紀錄，直接問我的時候會再查一次。"] })
    );
    render(<RemindersCard token="tok" onAsk={() => {}} />);

    expect(await screen.findByText("接下來 7 天沒有要特別注意的事。")).toBeInTheDocument();
    expect(screen.getByText(/這次沒有抓到你的時數紀錄/)).toBeInTheDocument();
  });

  it("拿不到提醒時整張卡片不顯示", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({ ok: false, status: 500 });
    const { container } = render(<RemindersCard token="tok" onAsk={() => {}} />);
    await vi.waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});
