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
function renderChatLoggedIn() {
  sessionStorage.setItem(
    "ncuxplore_session",
    JSON.stringify({ username: "test_user", token: "tok-123", hasActionAccess: true, threadId: "thread-1" })
  );

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
    vi.spyOn(global, "fetch").mockResolvedValue({ ok: true, json: async () => ({ status: "ok" }) });
  });

  it("登入狀態下正常顯示對話頁", async () => {
    renderChatLoggedIn();
    expect(await screen.findByText("你好！我是 NCUXplore，今天想查點什麼？")).toBeInTheDocument();
  });

  it("點登出不會讓整頁崩潰，會正確導回登入頁", async () => {
    const user = userEvent.setup();
    renderChatLoggedIn();

    await screen.findByText("你好！我是 NCUXplore，今天想查點什麼？");

    // 崩潰的話 userEvent.click 本身會把 render 過程中丟出的例外往外拋。
    await expect(user.click(screen.getByText("登出"))).resolves.not.toThrow();

    await waitFor(() => {
      expect(screen.getByText("登入頁")).toBeInTheDocument();
    });
  });

  it("開新對話會清空訊息、回到初始問候語", async () => {
    const user = userEvent.setup();
    renderChatLoggedIn();

    await screen.findByText("你好！我是 NCUXplore，今天想查點什麼？");

    const input = screen.getByPlaceholderText(/請輸入你的問題/);
    await user.type(input, "測試訊息");
    await user.click(screen.getByRole("button", { name: "送出" }));

    await waitFor(() => {
      expect(screen.getByText("測試訊息")).toBeInTheDocument();
    });

    await user.click(screen.getByText("開新對話"));

    await waitFor(() => {
      expect(screen.queryByText("測試訊息")).not.toBeInTheDocument();
    });
    expect(screen.getByText("你好！我是 NCUXplore，今天想查點什麼？")).toBeInTheDocument();
  });
});
