import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { SessionProvider } from "../../context/SessionContext";
import { ThemeProvider } from "../../context/ThemeContext";
import Chat from "../Chat";

// 回歸測試：登出當下 session 會先變成 null，Chat 還是會照樣重新 render
// 一次（React hooks 規則），如果元件裡任何一個 hook 在「導回登入頁」的
// guard 之前直接讀 session.xxx（不是 session?.xxx），這裡就會整個炸掉、
// 卡在空白畫面——這是使用者實際回報過的真實 bug，不是憑空想像的情境。
const STARTER = [
  { question: "我還差幾學分畢業？", label: "學業分析" },
  { question: "最近有什麼藝文活動？", label: "校園活動" },
];

function sseBody(events) {
  const text = events.map((e) => `data: ${JSON.stringify(e)}

`).join("");
  return new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(text));
      controller.close();
    },
  });
}

// 依網址回不同的假資料：推薦問題、聊天串流（每次呼叫都給一個新的 body）、重新查詢、其他（health 檢查等）。
function mockFetch({ streamEvents, refreshEvents } = {}) {
  return vi.spyOn(global, "fetch").mockImplementation(async (url) => {
    if (url === "/api/suggestions") {
      return { ok: true, json: async () => ({ questions: STARTER }) };
    }
    if (url === "/api/chat/stream" && streamEvents) {
      return { ok: true, body: sseBody(streamEvents) };
    }
    if (url === "/api/regulations/refresh" && refreshEvents) {
      return refreshEvents === 404 ? { ok: false, status: 404 } : { ok: true, body: sseBody(refreshEvents) };
    }
    return { ok: true, json: async () => ({ status: "ok" }) };
  });
}

function renderChat(session = { username: "test_user", token: "tok-123", threadId: "thread-1" }) {
  sessionStorage.setItem("ncuxplore_session", JSON.stringify(session));

  return render(
    <ThemeProvider>
      <SessionProvider>
        <MemoryRouter initialEntries={["/chat"]}>
          <Routes>
            <Route path="/chat" element={<Chat />} />
            <Route path="/login" element={<div>登入頁</div>} />
          </Routes>
        </MemoryRouter>
      </SessionProvider>
    </ThemeProvider>
  );
}

describe("Chat", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("登入狀態下一進來顯示歡迎畫面跟推薦問題", async () => {
    const fetchSpy = mockFetch();
    renderChat();

    expect(await screen.findByRole("button", { name: /我還差幾學分畢業？/ })).toBeInTheDocument();
    expect(screen.getByText("嗨，歡迎使用 NCUXplore！")).toBeInTheDocument();
    expect(screen.queryByText(/登入後還能查詢/)).not.toBeInTheDocument();
    const call = fetchSpy.mock.calls.find(([url]) => url === "/api/suggestions");
    expect(JSON.parse(call[1].body)).toEqual({ token: "tok-123", exclude: [] });
  });

  it("換一批會把目前顯示的題目告訴後端，避免又出現同一批", async () => {
    const user = userEvent.setup();
    const fetchSpy = mockFetch();
    renderChat();

    await user.click(await screen.findByRole("button", { name: /換一批/ }));

    await waitFor(() => {
      expect(fetchSpy.mock.calls.filter(([url]) => url === "/api/suggestions")).toHaveLength(2);
    });
    const [, lastCall] = fetchSpy.mock.calls.filter(([url]) => url === "/api/suggestions");
    expect(JSON.parse(lastCall[1].body).exclude).toEqual(STARTER.map((q) => q.question));
  });

  it("訪客模式會提示登入後能用的功能", async () => {
    mockFetch();
    renderChat({ username: "", token: "", threadId: "thread-1" });

    expect(await screen.findByText(/登入後還能查詢自己的課表/)).toBeInTheDocument();
  });

  it("點推薦問題會直接送出，歡迎畫面消失", async () => {
    const user = userEvent.setup();
    mockFetch({ streamEvents: [{ type: "result", content: "還差 54 學分" }, { type: "done" }] });
    renderChat();

    await user.click(await screen.findByRole("button", { name: /我還差幾學分畢業？/ }));

    expect(await screen.findByText("還差 54 學分")).toBeInTheDocument();
    expect(screen.getByText("我還差幾學分畢業？")).toBeInTheDocument();
    expect(screen.queryByText("嗨，歡迎使用 NCUXplore！")).not.toBeInTheDocument();
  });

  it("回答結束後顯示「你可能還想問」，點了會接著問", async () => {
    const user = userEvent.setup();
    mockFetch({
      streamEvents: [
        { type: "result", content: "回答內容" },
        { type: "done" },
        { type: "suggestions", questions: ["我的累計排名是多少？"] },
      ],
    });
    renderChat();

    await user.click(await screen.findByRole("button", { name: /最近有什麼藝文活動？/ }));
    await user.click(await screen.findByRole("button", { name: "我的累計排名是多少？" }));

    await waitFor(() => {
      expect(screen.getAllByText("回答內容")).toHaveLength(2);
    });
    expect(screen.getByText("我的累計排名是多少？", { selector: ".role-user *" })).toBeInTheDocument();
  });

  it("快取的法規回答按「重新查詢」，同一則訊息換成重新整理的回答", async () => {
    const user = userEvent.setup();
    const key = "c".repeat(32);
    const fetchSpy = mockFetch({
      streamEvents: [
        { type: "cached", key, created: "2026-10-18T21:03:11" },
        { type: "token", text: "補發要 200 元" },
        { type: "sources", sources: ["教務處/學生證遺失補發申請辦法.pdf"] },
        { type: "done" },
      ],
      refreshEvents: [
        { type: "status", text: "正在挑選相關文件..." },
        { type: "token", text: "補發要 250 元" },
        { type: "sources", sources: ["教務處/學生證遺失補發申請辦法.pdf"] },
        { type: "done" },
      ],
    });
    renderChat();

    await user.click(await screen.findByRole("button", { name: /最近有什麼藝文活動？/ }));
    expect(await screen.findByText("10/18 整理好的回答")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重新查詢這則回答" }));

    expect(await screen.findByText("補發要 250 元")).toBeInTheDocument();
    expect(screen.queryByText("補發要 200 元")).not.toBeInTheDocument();
    expect(screen.queryByText("10/18 整理好的回答")).not.toBeInTheDocument();  // 新的回答不是快取的
    const call = fetchSpy.mock.calls.find(([url]) => url === "/api/regulations/refresh");
    expect(JSON.parse(call[1].body)).toEqual({ key });
  });

  it("重新查詢找不到原本的問題時，請使用者直接再問一次", async () => {
    const user = userEvent.setup();
    mockFetch({
      streamEvents: [
        { type: "cached", key: "d".repeat(32), created: "2026-10-18T21:03:11" },
        { type: "token", text: "補發要 200 元" },
        { type: "done" },
      ],
      refreshEvents: 404,
    });
    renderChat();

    await user.click(await screen.findByRole("button", { name: /最近有什麼藝文活動？/ }));
    await user.click(await screen.findByRole("button", { name: "重新查詢這則回答" }));

    expect(await screen.findByText("找不到這則回答原本的問題，請直接再問一次。")).toBeInTheDocument();
  });

  it("點登出不會讓整頁崩潰，會正確導回登入頁", async () => {
    const user = userEvent.setup();
    mockFetch();
    renderChat();

    await screen.findByText("嗨，歡迎使用 NCUXplore！");

    // 崩潰的話 userEvent.click 本身會把 render 過程中丟出的例外往外拋。
    await expect(user.click(screen.getByText("登出"))).resolves.not.toThrow();

    await waitFor(() => {
      expect(screen.getByText("登入頁")).toBeInTheDocument();
    });
  });

  it("開新對話會清空訊息、回到歡迎畫面", async () => {
    const user = userEvent.setup();
    mockFetch();
    renderChat();

    await screen.findByText("嗨，歡迎使用 NCUXplore！");

    const input = screen.getByPlaceholderText(/請輸入你的問題/);
    await user.type(input, "測試訊息");
    await user.click(screen.getByRole("button", { name: "送出" }));

    await waitFor(() => {
      expect(screen.getByText("測試訊息")).toBeInTheDocument();
    });
    expect(screen.queryByText("嗨，歡迎使用 NCUXplore！")).not.toBeInTheDocument();

    await user.click(screen.getByText("開新對話"));

    await waitFor(() => {
      expect(screen.queryByText("測試訊息")).not.toBeInTheDocument();
    });
    expect(screen.getByText("嗨，歡迎使用 NCUXplore！")).toBeInTheDocument();
  });
});
