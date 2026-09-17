import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { SessionProvider, useSession } from "../SessionContext";

function Probe() {
  const { session, isLoggedIn, login, loginWithToken, unlockActionsWithPassword, logout, newConversation } =
    useSession();
  return (
    <div>
      <div data-testid="logged-in">{String(isLoggedIn)}</div>
      <div data-testid="username">{session?.username ?? ""}</div>
      <div data-testid="token">{session?.token ?? ""}</div>
      <div data-testid="chinese-name">{session?.chineseName ?? ""}</div>
      <div data-testid="has-action-access">{String(session?.hasActionAccess ?? false)}</div>
      <div data-testid="thread-id">{session?.threadId ?? ""}</div>
      <button onClick={() => login("test_user", "test_pass")}>login</button>
      <button onClick={() => loginWithToken("oauth_user", "oauth-token-456", "王小明")}>login-with-token</button>
      <button onClick={() => unlockActionsWithPassword("test_pass")}>unlock-actions</button>
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

  it("手動帳密登入後 hasActionAccess 為 true（已經建立了 Playwright session）", async () => {
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
      expect(screen.getByTestId("has-action-access")).toHaveTextContent("true");
    });
  });

  it("loginWithToken（OAuth callback 用）不打 API，hasActionAccess 一開始是 false", async () => {
    const fetchSpy = vi.spyOn(global, "fetch");

    const user = userEvent.setup();
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );

    await user.click(screen.getByText("login-with-token"));

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId("logged-in")).toHaveTextContent("true");
    expect(screen.getByTestId("username")).toHaveTextContent("oauth_user");
    expect(screen.getByTestId("token")).toHaveTextContent("oauth-token-456");
    expect(screen.getByTestId("chinese-name")).toHaveTextContent("王小明");
    expect(screen.getByTestId("has-action-access")).toHaveTextContent("false");
  });

  it("unlockActionsWithPassword 成功後把 hasActionAccess 補成 true，username 不變", async () => {
    const user = userEvent.setup();
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );

    // 先用 OAuth 方式登入（只有身分，還沒有 action 權限）
    await user.click(screen.getByText("login-with-token"));
    expect(screen.getByTestId("has-action-access")).toHaveTextContent("false");

    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ status: "success", token: "upgraded-token-789" }),
    });

    await user.click(screen.getByText("unlock-actions"));

    await waitFor(() => {
      expect(screen.getByTestId("has-action-access")).toHaveTextContent("true");
    });
    expect(screen.getByTestId("token")).toHaveTextContent("upgraded-token-789");
    expect(screen.getByTestId("username")).toHaveTextContent("oauth_user");
  });

  it("unlockActionsWithPassword 密碼錯誤時 hasActionAccess 維持 false", async () => {
    const user = userEvent.setup();
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );

    await user.click(screen.getByText("login-with-token"));

    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ status: "error", message: "登入失敗，請確認帳號密碼是否正確。" }),
    });

    await user.click(screen.getByText("unlock-actions"));

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });
    expect(screen.getByTestId("has-action-access")).toHaveTextContent("false");
  });
});
