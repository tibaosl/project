import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import CalendarExportButton from "../CalendarExportButton";

vi.mock("../../context/SessionContext", () => ({
  useOptionalSession: () => ({ session: { username: "110000000", token: "session-token" } }),
}));

describe("登入後的下載行事曆按鈕", () => {
  afterEach(() => vi.restoreAllMocks());

  it("帶 token 下載，沒排進課表時說明可能的原因", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ "X-Calendar-Contents": "calendar,activities" }),
      blob: async () => new Blob(["BEGIN:VCALENDAR"]),
    });
    URL.createObjectURL = vi.fn(() => "blob:calendar");
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    render(<CalendarExportButton />);
    fireEvent.click(screen.getByRole("button", { name: /下載課表跟校曆/ }));

    expect(await screen.findByRole("status")).toHaveTextContent("內容有校曆、已報名的活動。（這次沒有排進課表");
    expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({ token: "session-token" });
  });
});
