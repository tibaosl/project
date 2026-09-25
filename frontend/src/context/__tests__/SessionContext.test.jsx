import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { SessionProvider, useSession } from "../SessionContext";

function Probe() {
  const { session, isLoggedIn, login, loginWithChrome, logout, newConversation } = useSession();
  const [lastResult, setLastResult] = useState(null);
  return (
    <div>
      <div data-testid="logged-in">{String(isLoggedIn)}</div>
      <div data-testid="username">{session?.username ?? ""}</div>
      <div data-testid="token">{session?.token ?? ""}</div>
      <div data-testid="chinese-name">{session?.chineseName ?? ""}</div>
      <div data-testid="thread-id">{session?.threadId ?? ""}</div>
      <div data-testid="last-result">{lastResult ? JSON.stringify(lastResult) : ""}</div>
      <button onClick={() => login("test_user", "test_pass")}>login</button>
      <button onClick={async () => setLastResult(await login("test_user", ""))}>login-username-only</button>
      <button onClick={async () => setLastResult(await login("", "test_pass"))}>login-password-only</button>
      <button onClick={async () => setLastResult(await loginWithChrome())}>login-with-chrome</button>
      <button onClick={logout}>logout</button>
      <button onClick={newConversation}>new-conversation</button>
    </div>
  );
}

describe("SessionContext", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("登入成功後 isLoggedIn 為 true，存的是 token 不是密碼", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ status: "success", token: "fake-token-123" }),
    });

    const user = userEvent.setup();
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );

    await user.click(screen.getByText("login"));

    await waitFor(() => {
      expect(screen.getByTestId("logged-in")).toHaveTextContent("true");
    });
    expect(screen.getByTestId("username")).toHaveTextContent("test_user");
    expect(screen.getByTestId("token")).toHaveTextContent("fake-token-123");

    const stored = JSON.parse(sessionStorage.getItem("ncuxplore_session"));
    expect(stored.token).toBe("fake-token-123");
    expect(stored.password).toBeUndefined();
  });

  it("登入失敗（帳密錯誤）時 isLoggedIn 維持 false", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ status: "error", message: "登入失敗，請確認帳號密碼是否正確。" }),
    });

    const user = userEvent.setup();
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );

    await user.click(screen.getByText("login"));

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });
    expect(screen.getByTestId("logged-in")).toHaveTextContent("false");
    expect(sessionStorage.getItem("ncuxplore_session")).toBeNull();
  });

  it("登出後 isLoggedIn 為 false 且清掉 sessionStorage", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ status: "success", token: "fake-token-123" }),
    });

    const user = userEvent.setup();
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );

    await user.click(screen.getByText("login"));
    await waitFor(() => {
      expect(screen.getByTestId("logged-in")).toHaveTextContent("true");
    });

    await user.click(screen.getByText("logout"));

    expect(screen.getByTestId("logged-in")).toHaveTextContent("false");
    expect(sessionStorage.getItem("ncuxplore_session")).toBeNull();
  });

  it("先不登入（訪客模式）不會呼叫後端，也能標記為已登入", async () => {
    const fetchSpy = vi.spyOn(global, "fetch");

    function GuestProbe() {
      const { isLoggedIn, login } = useSession();
      return (
        <div>
          <div data-testid="logged-in">{String(isLoggedIn)}</div>
          <button onClick={() => login("", "")}>skip</button>
        </div>
      );
    }

    const user = userEvent.setup();
    render(
      <SessionProvider>
        <GuestProbe />
      </SessionProvider>
    );

    await user.click(screen.getByText("skip"));

    expect(screen.getByTestId("logged-in")).toHaveTextContent("true");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("開新對話會換一個新的 thread_id，但帳號不變", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ status: "success", token: "fake-token-123" }),
    });

    const user = userEvent.setup();
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );

    await user.click(screen.getByText("login"));
    await waitFor(() => {
      expect(screen.getByTestId("logged-in")).toHaveTextContent("true");
    });
    const firstThreadId = screen.getByTestId("thread-id").textContent;

    await user.click(screen.getByText("new-conversation"));
    const secondThreadId = screen.getByTestId("thread-id").textContent;

    expect(secondThreadId).not.toBe(firstThreadId);
    expect(screen.getByTestId("username")).toHaveTextContent("test_user");
  });

  it("Chrome 登入成功後存下後端回傳的帳號、姓名跟 token，不會有密碼", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ status: "success", token: "chrome-token-456", username: "113000000", chinese_name: "王小明" }),
    });

    const user = userEvent.setup();
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );

    await user.click(screen.getByText("login-with-chrome"));

    await waitFor(() => {
      expect(screen.getByTestId("logged-in")).toHaveTextContent("true");
    });
    expect(global.fetch).toHaveBeenCalledWith("/api/login/chrome", { method: "POST" });
    expect(screen.getByTestId("username")).toHaveTextContent("113000000");
    expect(screen.getByTestId("token")).toHaveTextContent("chrome-token-456");
    expect(screen.getByTestId("chinese-name")).toHaveTextContent("王小明");

    const stored = JSON.parse(sessionStorage.getItem("ncuxplore_session"));
    expect(stored.password).toBeUndefined();
  });

  it("Chrome 登入失敗（逾時、取消）時維持未登入，並回傳錯誤訊息", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ status: "error", message: "登入失敗：3 分鐘內沒有在 Chrome 視窗完成登入。" }),
    });

    const user = userEvent.setup();
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );

    await user.click(screen.getByText("login-with-chrome"));

    await waitFor(() => {
      const result = JSON.parse(screen.getByTestId("last-result").textContent);
      expect(result).toEqual({ ok: false, message: "登入失敗：3 分鐘內沒有在 Chrome 視窗完成登入。" });
    });
    expect(screen.getByTestId("logged-in")).toHaveTextContent("false");
    expect(sessionStorage.getItem("ncuxplore_session")).toBeNull();
  });

  it("手動登入只填帳號沒填密碼時回傳錯誤，不會落到訪客模式", async () => {
    const fetchSpy = vi.spyOn(global, "fetch");

    const user = userEvent.setup();
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );

    await user.click(screen.getByText("login-username-only"));

    await waitFor(() => {
      const result = JSON.parse(screen.getByTestId("last-result").textContent);
      expect(result.ok).toBe(false);
    });
    expect(screen.getByTestId("logged-in")).toHaveTextContent("false");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("手動登入只填密碼沒填帳號時也回傳錯誤，不會落到訪客模式", async () => {
    const user = userEvent.setup();
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );

    await user.click(screen.getByText("login-password-only"));

    await waitFor(() => {
      const result = JSON.parse(screen.getByTestId("last-result").textContent);
      expect(result.ok).toBe(false);
    });
    expect(screen.getByTestId("logged-in")).toHaveTextContent("false");
  });
});
